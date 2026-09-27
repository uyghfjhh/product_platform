-- 测试hint模式下，读切换标签的dml操作和select操作
SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;

show port;

SELECT inet_server_addr();

select * from test where id = 13;

update test set name = 'o' where id = 13;

delete from test where id = 13;

insert into test values (14,'name');
