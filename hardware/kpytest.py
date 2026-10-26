"""Run pytest under KiCad's Python (which ignores PYTHONPATH): kpython hardware/kpytest.py [args]."""
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(here, "..", "tools", "pylib"), here]
import pytest
sys.exit(pytest.main(sys.argv[1:] or [os.path.join(here, "tests")]))
