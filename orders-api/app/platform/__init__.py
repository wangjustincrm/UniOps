"""Platform foundation for this service (design §13). See README.md.

Never imports business code (`app.*` outside `app.platform`); the check is
tests/platform/test_one_way_dependency.py.
"""
