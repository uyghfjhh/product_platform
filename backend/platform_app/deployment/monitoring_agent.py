"""Read-only Linux resource probe; remote Python 3.6 compatible."""
import json
import os
import socket
import sys
import time


def collect(request):
    result={'hostname':socket.gethostname(),'sample_epoch':time.time(),'filesystems':[],'devices':{}}
    with open('/proc/sys/kernel/random/boot_id') as handle:
        result['boot_id']=handle.read().strip()
    with open('/proc/stat') as handle:
        cpu=[int(v) for v in handle.readline().split()[1:9]]
    result['cpu_total']=sum(cpu);result['cpu_idle']=cpu[3]+cpu[4];result['cpu_iowait']=cpu[4];result['cpu_count']=os.cpu_count()
    memory={}
    with open('/proc/meminfo') as handle:
        for line in handle:
            name,value=line.split(':',1)
            if name in ('MemTotal','MemAvailable'):
                memory[name]=int(value.split()[0])*1024
    result['memory_total']=memory.get('MemTotal');result['memory_available']=memory.get('MemAvailable')
    with open('/proc/uptime') as handle:
        result['uptime_seconds']=float(handle.read().split()[0])
    with open('/proc/loadavg') as handle:
        result['load_average']=[float(v) for v in handle.read().split()[:3]]
    names=set(os.listdir('/sys/block'))
    with open('/proc/diskstats') as handle:
        for line in handle:
            values=line.split();name=values[2]
            if name in names and not name.startswith(('loop','ram')):
                result['devices'][name]={'read_sectors':int(values[5]),'write_sectors':int(values[9])}
    filesystems={}
    for path in request.get('paths',[]):
        try:
            actual=os.path.realpath(path);stats=os.statvfs(actual);device=str(os.stat(actual).st_dev)
            item=filesystems.setdefault(device,{'device':device,'paths':[],'total_bytes':stats.f_blocks*stats.f_frsize,'available_bytes':stats.f_bavail*stats.f_frsize})
            item['paths'].append(path)
        except OSError as exc:
            result['filesystems'].append({'paths':[path],'error':str(exc)})
    result['filesystems'].extend(filesystems.values())
    return result


if __name__=='__main__':
    try:
        print(json.dumps(collect(json.loads(sys.argv[1]))))
    except Exception as exc:
        print(json.dumps({'error':str(exc)}))
