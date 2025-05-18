from samsungtvws import SamsungTVWS

tv = SamsungTVWS(
    host="192.168.2.142",
    name="screen-machine",
    port=8002,  # port 8002 is for encrypted connection, try 8001 if this fails
    token_file='/south-screen-token.txt'
    # don't pass token_file yet
)

tv.shortcuts().home()  # Or use .power() to trigger connection




tv = SamsungTVWS(
    host="192.168.2.61",
    name="screen-machine",
    port=8002,  # port 8002 is for encrypted connection, try 8001 if this fails
    token_file='/north-screen-token.txt'
    # don't pass token_file yet
)

tv.shortcuts().home()  # Or use .power() to trigger connection