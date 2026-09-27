SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;
select ID from table_test where id = 1;
SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE;
update table_test set data ='ddd' where id =5;
