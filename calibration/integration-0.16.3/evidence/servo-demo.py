#!/usr/bin/env python3
"""Explicit synthetic browser harness, not a production data source or hardware evidence."""
import json
import math
from pathlib import Path
import sys
import tempfile
import threading
import time

I=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(I/'web'))
import calibration_web as web


def main():
    with tempfile.TemporaryDirectory(prefix='servo-synthetic-') as tmp:
        directory=Path(tmp)
        report=dict(schema='microduck-servo-maintenance/v1',robot_label='SYNTHETIC NOT HARDWARE',
            collected_at='2026-10-26T00:00:00+00:00',servos=[dict(id=i,raw={'0':1200,'6':52,'63':52,'64':0,'70':0,
            '31':70,'32':70,'34':35,'38':1750,'144':50,'146':32},read_errors=[]) for i in web.servo_health.IDS])
        (directory/'servo-maintenance-synthetic.json').write_text(json.dumps(report,indent=2)+'\n')
        args=web.parser().parse_args(['--target','daemon-v0.16.3','--demo','--no-telemetry','--enable-servo-health',
            '--config',str(I/'evidence/demo/robotd.toml'),'--dest',str(directory/'cal.toml'),
            '--servo-health-dir',str(directory/'observer'),'--port','18766'])
        ctx=web.Context(args);engine=ctx.servo.engine;stop=threading.Event()
        engine.baselines={'SYNTHETIC stand':dict(p95_ma=[100.]*15,version=1,source='synthetic_fixture')}
        engine.on_health(dict(motors={'max_c':42,'mean_c':32,'hottest':'left_knee'},battery={'volts':5.},bus={'consecutive_errors':0}))
        def feed():
            while not stop.is_set():
                t=time.monotonic();row=dict(t_ns=time.monotonic_ns(),policy='SYNTHETIC stand',safety={},
                    currents_ma=[40+12*math.sin(t+i) for i in range(15)],velocities=[0.]*15,joints=[0.]*15,targets=[0.]*15)
                row['currents_ma'][3]=360+10*math.sin(t);row['targets'][3]=math.radians(18)
                ctx.servo.submit(row);stop.wait(.2)
        worker=threading.Thread(target=feed,daemon=True);worker.start()
        server=web.ThreadingHTTPServer(('127.0.0.1',args.port),web.new_handler(ctx))
        print('SYNTHETIC ONLY http://127.0.0.1:18766/',flush=True)
        try:server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:pass
        finally:stop.set();worker.join();server.server_close();ctx.monitor.close();ctx.servo.close()


if __name__=='__main__':main()
