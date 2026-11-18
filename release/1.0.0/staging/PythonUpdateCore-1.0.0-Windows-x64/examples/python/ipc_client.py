import json, subprocess, sys
p = subprocess.Popen([sys.argv[1], "serve", "--stdio", "--data-root", sys.argv[2]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
def call(i, command, params={}):
    p.stdin.write(json.dumps({"protocol_version":1,"type":"request","request_id":str(i),"command":command,"params":params})+"\n"); p.stdin.flush()
    print(p.stdout.readline().rstrip())
call(1,"initialize")
call(2,"get_version")
call(3,"shutdown")
p.wait(timeout=10)
