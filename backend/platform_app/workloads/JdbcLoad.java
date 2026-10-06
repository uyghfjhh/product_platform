import java.sql.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
public class JdbcLoad {
 public static void main(String[] args) throws Exception {
  int clients=Integer.parseInt(args[0]),seconds=Integer.parseInt(args[1]);
  int queryTimeout=Integer.parseInt(System.getenv().getOrDefault("JDBC_STATEMENT_TIMEOUT", "10"));
  long started=System.nanoTime(),deadline=started+seconds*1000000000L;
  AtomicLong count=new AtomicLong(),nanos=new AtomicLong(),failed=new AtomicLong();
  ExecutorService pool=Executors.newFixedThreadPool(clients);
  for(int i=0;i<clients;i++) pool.submit(()->{
   try(Connection c=DriverManager.getConnection(System.getenv("JDBC_URL"),System.getenv("JDBC_USER"),System.getenv().getOrDefault("JDBC_PASSWORD",""));Statement s=c.createStatement()) {
    c.setAutoCommit(false);c.setReadOnly(true);s.setQueryTimeout(queryTimeout);
    while(System.nanoTime()<deadline) {long start=System.nanoTime();try(ResultSet r=s.executeQuery(System.getenv("JDBC_SQL"))) {while(r.next()) {}} c.commit();count.incrementAndGet();nanos.addAndGet(System.nanoTime()-start);}
   } catch(Exception e) {failed.incrementAndGet();}
  });
  long lastCount=0,lastNanos=0,lastSampleAt=started;
  for(int second=1;second<=seconds;second++) {
   Thread.sleep(1000);long n=count.get(),t=nanos.get(),delta=n-lastCount,observedAt=System.nanoTime();
   double interval=(observedAt-lastSampleAt)/1000000000.0;
   System.out.printf(java.util.Locale.ROOT,"{\"type\":\"sample\",\"elapsed\":%.6f,\"tps\":%.6f,\"latency_ms\":%.6f,\"transactions\":%d}%n",(observedAt-started)/1000000000.0,delta/interval,delta==0?0:(t-lastNanos)/1000000.0/delta,n);
   System.out.flush();lastCount=n;lastNanos=t;lastSampleAt=observedAt;
  }
  pool.shutdown();if(!pool.awaitTermination(queryTimeout+5L,TimeUnit.SECONDS)) {pool.shutdownNow();failed.incrementAndGet();}
  double elapsed=(System.nanoTime()-started)/1000000000.0;
  System.out.printf(java.util.Locale.ROOT,"{\"type\":\"summary\",\"elapsed\":%.6f,\"tps\":%.6f,\"latency_ms\":%.6f,\"transactions\":%d,\"errors\":%d}%n",elapsed,count.get()/elapsed,count.get()==0?0:nanos.get()/1000000.0/count.get(),count.get(),failed.get());
  if(failed.get()>0)System.exit(1);
 }
}
