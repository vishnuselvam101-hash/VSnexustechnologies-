import platform,sys,subprocess
def environment():
 try: commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
 except Exception: commit=None
 return {'python':sys.version,'os':platform.platform(),'cpu':platform.processor() or platform.machine(),'git_commit':commit}
