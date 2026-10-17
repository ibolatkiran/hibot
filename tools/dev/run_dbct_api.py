"""Start DbChangeTracking API locally against the shared preprod DB with the safe local-run switches.
Secrets are read from files and passed only via the child process environment; nothing is printed."""
import json, os, re, subprocess, sys
API = r"D:\repos\DbChangeTrackingSystemAPI\DbChangeTrackingSystem.API"
sys.path.insert(0, r"D:\repos\HiBot")
from hibot.envfile import load_env
load_env()
cfg = json.loads(re.sub(r"^\s*//.*$", "", open(os.path.join(API, "appsettings.json"), encoding="utf-8-sig").read(), flags=re.M))
env = dict(os.environ,
           ASPNETCORE_ENVIRONMENT="Development",
           DOTNET_ROLL_FORWARD="Major",
           Monitoring__ConfigurationDatabase__ConnectionString=cfg["ConnectionStrings"]["GfsmConnection"],
           HiBot__ServiceKey=os.environ["HIBOT_SERVICE_KEY"],
           Hangfire__ServerQueues__0="hibot",
           Monitoring__Scheduling__SyncOnStartup="false",
           Monitoring__Bootstrap__Enabled="false")
sys.exit(subprocess.call(["dotnet", "run", "--launch-profile", "https"], cwd=API, env=env))
