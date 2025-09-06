"""
A series of integration tests which center around:

- toggling auth functionality (nofunmode)
- testing auth with concurrent API calls
- testing various public/restricted endpoints during nofunmode
- restarting server and confirm login security changes are applied
- ensuring correct 401 errors when incorrect auth/malformed auth/no auth is provided

"""
