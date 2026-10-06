import java.sql.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
public class JdbcLoad {
 public static void main(String[] args) throws Exception {
  int clients=Integer.parseInt(args[0]),seconds=Integer.parseInt(args[1]);
  long deadline=System.nanoTime()+seconds*1000000000L;
  AtomicLong count=new AtomicLong(),nanos=new AtomicLong(),failed=new AtomicLong();
  ExecutorService pool=Executors.newFixedThreadPool(clients);
  for(int i=0;i<clients;i++) pool.submit(()->{
   try(Connection c=DriverManager.getConnection(System.getenv("JDBC_URL"),System.getenv("JDBC_USER"),System.getenv().getOrDefault("JDBC_PASSWORD",""));Statement s=c.createStatement()) {
    c.setAutoCommit(false);c.setReadOnly(true);s.setQueryTimeout(Integer.parseInt(System.getenv().getOrDefault("JDBC_STATEMENT_TIMEOUT", "10")));
    while(System.nanoTime()<deadline) {long start=System.nanoTime();try(ResultSet r=s.executeQuery(System.getenv("JDBC_SQL"))) {while(r.next()) {}} c.commit();count.incrementAndGet();nanos.addAndGet(System.nanoTime()-start);}
   } catch(Exception e) {failed.incrementAndGet();}
  });
  long lastCount=0,lastNanos=0;
  for(int second=1;second<=seconds;second++) {
   Thread.sleep(1000);long n=count.get(),t=nanos.get(),delta=n-lastCount;
   System.out.printf(java.util.Locale.ROOT,"{\"type\":\"sample\",\"elapsed\":%d,\"tps\":%d,\"latency_ms\":%.6f,\"transactions\":%d}%n",second,delta,delta==0?0:(t-lastNanos)/1000000.0/delta,n);
   System.out.flush();lastCount=n;lastNanos=t;
  }
  pool.shutdown();if(!pool.awaitTermination(15,TimeUnit.SECONDS))pool.shutdownNow();
  System.out.printf(java.util.Locale.ROOT,"{\"type\":\"summary\",\"tps\":%.6f,\"latency_ms\":%.6f,\"transactions\":%d,\"errors\":%d}%n",count.get()/(double)seconds,count.get()==0?0:nanos.get()/1000000.0/count.get(),count.get(),failed.get());
  if(failed.get()>0)System.exit(1);
 }
}
