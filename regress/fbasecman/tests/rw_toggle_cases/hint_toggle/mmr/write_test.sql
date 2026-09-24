-- 测试hint模式下，不带读写切换标签的dml操作和select操作 
show port;

SELECT inet_server_addr();

insert into test values (12,'name');
insert into test values (13,'name');

select * from test where id = 12;

update test set name = 'p' where id =12;

select * from test where id = 12;

delete from test where id = 12;

select * from test where id = 12;

delete from test where id =13;

select * from test where id = 13;