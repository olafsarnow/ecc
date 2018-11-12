#!/usr/bin/python3
# 
# 
# 
# 
# Kim Brugger (20 Sep 2018), contact: kim@brugger.dk

import os
import sys
import pprint
pp = pprint.PrettyPrinter(indent=4)
import time
import datetime
import argparse
import re
import tempfile
import traceback

import logging
logger = logging.getLogger('ehosd')


logging.getLogger('urllib3').setLevel(logging.CRITICAL)
logging.getLogger('keystoneauth').setLevel(logging.CRITICAL)
logging.getLogger('stevedore').setLevel(logging.CRITICAL)
logging.getLogger('concurrent').setLevel(logging.CRITICAL)
logging.getLogger('openstack').setLevel(logging.CRITICAL)
logging.getLogger('dogpile').setLevel(logging.CRITICAL)


# python3+ is broken on centos 7, so add the /usr/local/paths by hand
sys.path.append("/usr/local/lib/python{}.{}/site-packages/".format( sys.version_info.major, sys.version_info.minor))
sys.path.append("/usr/local/lib64/python{}.{}/site-packages/".format( sys.version_info.major, sys.version_info.minor))


from munch import Munch

import ehos
import ehos.htcondor
import ehos.monitor as monitor


monitor.connect("postgresql://ehos:ehos@127.0.0.1:5432/ehos_monitor")


MONITORING = True

condor = ehos.htcondor.Condor()


def create_execute_config_file(master_ip:str, uid_domain:str, password:str, outfile:str='/usr/local/etc/ehos/execute.yaml', execute_config:str=None):
    """ Create a execute node config file with the master ip and pool password inserted into it

    Args:
      master_ip: ip of the master to connect to
      uid_domain: domain name we are using for this
      password: for the cloud
      outfile: file to write the execute file to
      execute_config: define config template, otherwise find it in the system

    Returns:
      file name with path (str)

    Raises:
      None

    """

    if ( execute_config is None):
        execute_config = ehos.find_config_file('execute.yaml')
    
    ehos.alter_file(execute_config, outfile=outfile, patterns=[ (r'{master_ip}',master_ip),
                                                                (r'{uid_domain}', uid_domain),
                                                                (r'{password}',password)])
                                                

    return outfile

                    


def htcondor_setup_config_file( ):
    """ checks if this is a new instance of the ehosdm if it is tweak the htcondor config file and reload it

    Args:
      htcondor (obj): ehos.htcondor class

    Returns:
      None
    
    Raises:
      None
    """

    
    host_ip    = ehos.get_host_ip()

    uid_domain = ehos.make_uid_domain_name(5)

    # first time running this master, so tweak the personal configureation file
    if ( os.path.isfile( '/etc/condor/00personal_condor.config')):

         ehos.alter_file(filename='/etc/condor/00personal_condor.config', patterns=[ (r'{master_ip}',host_ip),
                                                                                     (r'{uid_domain}',uid_domain)])

         os.rename('/etc/condor/00personal_condor.config', '/etc/condor/config.d/00personal_condor.config')

         # re-read configuration file
         ehos.htcondor.reload_config()

    


def run_daemon( config_file:str="/usr/local/etc/ehos_master.yaml" ):
    """ Creates the ehos daemon loop that creates and destroys nodes etc.
               
    The confirguration file is continously read so it is possible to tweak the behaviour of the system
               
    Args:
      init_file: alternative config_file

    Returns:
      None

    Raises:
      None
    """


    config = ehos.readin_config_file( config_file )

    ehos.init()
    ehos.connect_to_clouds( config )
    

    htcondor_setup_config_file()
    
    host_ip    = ehos.get_host_ip( )

    uid_domain = ehos.make_uid_domain_name(5)

#    htcondor_security.setPoolPassword( config.condor.password )

    execute_config_file = create_execute_config_file( host_ip, uid_domain, config.condor.password )


    while ( True ):

        config = ehos.readin_config_file( config_file )

#        ehos.update_node_states()
        
        # get the current number of nodes
        nodes  = ehos.update_node_states()
        jobs   = condor.job_counts()



        # send some data off to the monitoring system
        if ( MONITORING ):
            for cloud in nodes:
                for state in nodes[ cloud ]:
                    monitor.add_stat(context = state, target = cloud, value= nodes[ cloud ][state])

            for job_state in jobs:
                monitor.add_stat( context = job_state, target = 'queue', value=jobs[ job_state ])

                
        # just care about the overall number of nodes, not how many in each cloud
        nodes = Munch(nodes[ 'all' ])

        logger.debug( "Node data\n" + pp.pformat( nodes ))
        logger.debug( "Jobs data\n" + pp.pformat( jobs  ))

        logger.info("Nr of nodes {} ({} are idle)".format( nodes.total, nodes.idle))
        logger.info("Nr of jobs {} ({} are queueing)".format( jobs.total, jobs.idle))
        
        # Below the min number of nodes needed for our setup
        if ( nodes.total < config.ehos_daemon.nodes_min ):
            logger.info("We are below the min number of nodes, creating {} nodes".format( config.ehos_daemon.nodes_min - nodes.total))

            monitor.add_event(context = 'create_nodes', target = 'master', value= config.ehos_daemon.nodes_min - nodes.total)
            ehos.create_execute_nodes(config, execute_config_file, config.ehos_daemon.nodes_min - nodes.total)

        ### there are jobs queuing, let see what we should do

        # got jobs in the queue but less than or equal to our idle + spare nodes, do nothing
        elif (  jobs.idle and jobs.idle <= nodes.idle ):
            logger.info("We got stuff to do, but seems to have enough nodes to cope...")


            
        # Got room to make some additional nodes
        elif (  jobs.idle and nodes.total + config.ehos_daemon.nodes_spare <= config.ehos_daemon.nodes_max ):
            
            logger.info("We got stuff to do, creating some additional nodes...")

            monitor.add_event(context = 'create_nodes', target = 'master', value= config.ehos_daemon.nodes_max - nodes.total)
            ehos.create_execute_nodes(config, execute_config_file, config.ehos_daemon.nodes_max - nodes.total )

        # this one is just a sanity one
        elif ( jobs.idle and nodes.total == config.ehos_daemon.nodes_max):
            logger.info("We are busy. but all nodes we are allowed have been created, nothing to do")


        ### Looks like we have an excess of nodes, lets cull some

        # We got extra nodes not needed and we can delete some without going under the min cutoff, so lets get rid of some
        elif ( nodes.total > config.ehos_daemon.nodes_min and
               nodes.idle  > config.ehos_daemon.nodes_spare ):

            nr_of_nodes_to_delete = min( nodes.total - config.ehos_daemon.nodes_min, nodes.idle - config.ehos_daemon.nodes_spare)
            
            logger.info("Deleting {} idle nodes...".format( nr_of_nodes_to_delete))
            monitor.add_event(context = 'delete_nodes', target = 'master', value= nr_of_nodes_to_delete )
            ehos.delete_idle_nodes(nr_of_nodes_to_delete)
            
        else:
            logger.info("The number of execute nodes are running seem appropriate, nothing to change.")

        logger.info("Napping for {} second(s).".format(config.ehos_daemon.sleep_min))
        time.sleep( config.ehos_daemon.sleep_min)


        
def main():
    """ main loop

    Args:
      None
    
    Returns:
      None
    
    Raises: 
      None
    """

    parser = argparse.ArgumentParser(description='ehosd: the ehos daemon to be run on the master node ')

    parser.add_argument('-v', '--verbose', default=1, action="count",  help="Increase the verbosity of logging output")
    parser.add_argument('config_file', metavar='config-file', nargs='?',    help="yaml formatted config file", default=[ehos.find_config_file('ehos.yaml')])


    args = parser.parse_args()

    # as this is an array, and we will ever only get one file set it
#    args.config_file = args.config_file[ 0 ]


    ehos.log_level( args.verbose )
    logger.info("Running with config file: {}".format( args.config_file))


    if ( args.config_file):
        run_daemon( args.config_file )
    else:
        run_daemon(  )



if __name__ == '__main__':
    main()
else:
    print("Not to be run as a library")
    sys.exit( 1 )
          
    
