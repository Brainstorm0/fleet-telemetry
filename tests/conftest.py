import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "lambdas", "processor"))
sys.path.insert(0, os.path.join(ROOT, "simulator"))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
