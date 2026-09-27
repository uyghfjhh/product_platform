BEGIN READ ONLY;

show port;
SELECT inet_server_addr();

COMMIT;


SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE;

show port;
SELECT inet_server_addr();

