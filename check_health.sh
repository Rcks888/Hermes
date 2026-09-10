#!/bin/bash
echo "=== Memory ==="
free -m
echo ""
echo "=== Top Processes by Memory ==="
ps aux --sort=-%mem | head -10
echo ""
echo "=== Load Average ==="
uptime
echo ""
echo "=== Hermes Soak Status ==="
python3 -c "
import json
with open('/root/Hermes/logs/soak.json') as f:
    d = json.load(f)
total = len(d['cycles'])
ok = sum(1 for c in d['cycles'] if c['connected'])
latencies = [c.get('init_latency_s',0) for c in d['cycles'] if c.get('init_latency_s')]
avg_lat = sum(latencies)/len(latencies) if latencies else 0
max_lat = max(latencies) if latencies else 0
ram_free = [c.get('resources',{}).get('ram_free_mb',0) for c in d['cycles'] if c.get('resources')]
min_ram = min(ram_free) if ram_free else 0
print(f'Cycles: {total} | OK: {ok} | Uptime: {ok/total*100:.1f}%')
print(f'Init latency: avg={avg_lat:.1f}s max={max_lat:.1f}s')
print(f'Min free RAM during soak: {min_ram}MB')
"
