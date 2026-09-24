SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;
select data from table_test where id = 1;
SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE;
update table_test set data ='aaa' where id = 3;
