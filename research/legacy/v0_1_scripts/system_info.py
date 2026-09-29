import json,platform,os
print(json.dumps({'os':platform.platform(),'python':platform.python_version(),'architecture':platform.machine(),'cpu_count':os.cpu_count()},indent=2))
