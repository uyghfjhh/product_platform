-- 测试hint模式下，不带读写切换标签的dml操作和select操作  
-- 用于测试写切换到读，再由读切换到写的功能
show port;

SELECT inet_server_addr();

insert into test values (12,'name');
insert into test values (13,'name');

select * from test where id = 12;
select * from test where id = 13;

-- 测试hint模式下，读切换标签的dml操作和select操作
SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;

show port;

SELECT inet_server_addr();

select * from test where id = 1;

insert into test values (14,'name');

delete from test where id = 14;

select pg_sleep(2);

-- 测试hint模式下，写切换标签的dml操作和select操作
SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE;

show port;

SELECT inet_server_addr();

select * from test where id = 12;

delete from test where id in (12,13);

