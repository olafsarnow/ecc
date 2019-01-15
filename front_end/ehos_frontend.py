#!/usr/bin/python3
""" 
 
 
 
 Kim Brugger (12 Nov 2018), contact: kim@brugger.dk
"""

import sys
import pprint
pp = pprint.PrettyPrinter(indent=4)



import re
import os

# python3+ is broken on centos 7, so add the /usr/local/paths by hand
sys.path.append("/usr/local/lib/python{}.{}/site-packages/".format( sys.version_info.major, sys.version_info.minor))
sys.path.append("/usr/local/lib64/python{}.{}/site-packages/".format( sys.version_info.major, sys.version_info.minor))


from flask import Flask
from flask import render_template




from munch import Munch

sys.path.append("/tmp/ehos-python/")

pp.pprint( sys.path )

import ehos

import ehos.htcondor
import ehos.instances
import ehos.monitor as monitor
monitor.connect("postgresql://ehos:ehos@127.0.0.1:5432/ehos_monitor")


app = Flask(__name__)



def wrap_strings( a ):

    res = []

    for i in a:
        res.append( "\"{}\"".format( i ))
    return res
    



@app.route('/')
@app.route('/<lag>')
def index(lag=10):


    try:
        lag = int( lag )
    except:
        lag = 10

    host_id    = '6f4967d5-706e-4f58-8287-74796c8fff26'
    host_ip    = '158.37.63.101'

    context = {'host_id': host_id,
               'host_ip': host_ip,
               'nodes': {}}
    

    
    i = ehos.instances.Instances()
    i.connect( 'postgresql://ehos:ehos@127.0.0.1:5432/ehos_instances')

    nodes = i.node_list_db()
    
    for cloud in nodes:
        context['nodes'][cloud] = []
        for node in nodes[ cloud ]:
            if node[ 'state'] not in ['active', 'booting', 'retiring']:
                continue 
            context['nodes'][ cloud ].append( node )

    
    
    
    if ( 10 ):
        ehos.init()
        condor  = ehos.htcondor.Condor()
        context['queue']  = condor.job_counts()
    else:
        context['queue'] = {'idle': 10, 'running': 2, 'total': 12}


    context['queue']['idle_p'] = 0
    context['queue']['running_p'] = 0
    
    if ( context['queue']['total'] > 0 ):
        context['queue']['idle_p'] =  context['queue']['idle']/context['queue']['total']*100.0
        context['queue']['running_p'] =  context['queue']['running']/context['queue']['total']*100.0

    keys = {'all-idle': 'Nodes idle',
            'all-busy': 'Node busy',
            'queue-running': 'Jobs running',
            'queue-idle': 'Jobs waiting',
            'all-total': 'Nodes total'}
        

    if ( lag == 5 ):
        time_series = monitor.timeserie_5min(keys=list(keys.keys()), method='median')
    elif ( lag == 10 ):
        time_series = monitor.timeserie_10min(keys=list(keys.keys()), method='median')
    elif ( lag == 15 ):
        time_series = monitor.timeserie_15min(keys=list(keys.keys()), method='median')
    elif( lag == 30 ):
        time_series = monitor.timeserie_30min(keys=list(keys.keys()), method='median')
    elif( lag == 60 ):
        time_series = monitor.timeserie_1hour(keys=list(keys.keys()), method='median')
    else:
        time_series = monitor.timeserie_10min(keys=list(keys.keys()), method='median')


    context['graph'] = {}

#    pp.pprint( time_series )
    
    max_value =   monitor.timeserie_max_value( time_series )
    context['graph'][ 'max_y'] = max_value + 2

    time_series = monitor.transform_timeserie_to_dict( time_series )
    
    
    time_series[ 'x'] = wrap_strings(time_series[ 'x'] )    
    context['graph']['labels'] = "[{}]".format(",".join( time_series[ 'x'] ))


    context['graph']['datasets'] = []

    data_template = "label: '{name}', fill: 0, borderColor: '{colour}',  data: {data},\n"

    if 'all-total' in time_series:
        context['graph']['datasets'].append( data_template.format(name='Nodes total',  colour="rgb(230, 230, 230)", data=time_series['all-total']))
#    if 'all-idle' in time_series:
#        context['graph']['datasets'].append( data_template.format(name='Nodes idle',   colour="rgb(200, 200, 200)", data=time_series['all-idle']))
    if 'all-busy' in time_series:
        context['graph']['datasets'].append( data_template.format(name='Nodes busy',   colour="rgb(150,   0,   0)", data=time_series['all-busy']))
    if 'queue-idle' in time_series:
        context['graph']['datasets'].append( data_template.format(name='Jobs waiting', colour="rgb(255, 128,   0)", data=time_series['queue-idle']))
    if 'queue-running' in time_series:
        context['graph']['datasets'].append( data_template.format(name='Jobs Running', colour="rgb(  0, 200,   0)", data=time_series['queue-running']))

    
    return render_template('ehos/index.html', info=context)



if __name__ == '__main__':


    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

