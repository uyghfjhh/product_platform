/* Test-only link wrappers. No production source changes or product failpoints.
 * Link against the product's objects with --wrap; all observations come from
 * real cache/outstanding/iov implementations. Each test owns its proxy.
 */
#include <kiwi.h>
#include <machinarium.h>
#include <odyssey.h>
#include <fb_guc_cache.h>
#include <fb_outstanding_request.h>
#include <execinfo.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdatomic.h>

static _Atomic int injected;
static char *esc(const char *s) {
    static _Thread_local char bufs[8][1024];
    static _Thread_local unsigned slot;
    char *out = bufs[(slot++) % 8]; size_t n=0;
    if (!s) s="";
    for (; *s && n < 1000; ++s) {
        if (*s=='"' || *s=='\\') out[n++]='\\';
        if ((unsigned char)*s < 32) { out[n++]=' '; continue; }
        out[n++]=*s;
    }
    out[n]=0; return out;
}
static void stage(char *out, size_t size) {
    const char *name=getenv("FB_GUC_TEST_STAGE"); out[0]=0;
    if (!name) return;
    int fd=open(name,O_RDONLY); if(fd<0) return;
    ssize_t n=read(fd,out,size-1); close(fd);
    if(n>0) out[n]=0;
}
static void event(const char *kind, fb_guc_cache_t *cache, const char *key,
                  const char *value, int result) {
    const char *name=getenv("FB_GUC_TEST_TRACE"); if(!name) return;
    char phase[80], line[4096]; stage(phase,sizeof(phase));
    int n=snprintf(line,sizeof(line),"{\"event\":\"%s\",\"stage\":\"%s\",\"cache\":\"%s\",\"address\":\"%p\",\"key\":\"%s\",\"value\":\"%s\",\"result\":%d}\n",
        esc(kind),esc(phase),esc(cache?cache->cache_name:""),(void*)cache,esc(key),esc(value),result);
    int fd=open(name,O_CREAT|O_WRONLY|O_APPEND,0600);
    if(fd>=0) { if(n>0 && n<(int)sizeof(line)) (void)write(fd,line,n); close(fd); }
}
static int fail(const char *point) {
    const char *wanted=getenv("FB_GUC_TEST_FAIL"); char phase[80];stage(phase,sizeof(phase));
    if(!wanted || strcmp(wanted,point) || strncmp(phase,"fault",5)) return 0;
    int expected=0; if(!atomic_compare_exchange_strong(&injected,&expected,1)) return 0;
    event("injected",NULL,point,"",0); errno=ENOMEM;return 1;
}
static int execute_stack(void) {
    void *frames[32]; int count=backtrace(frames,32);
    for(int i=1;i<count;i++) {
        Dl_info info;
        if(dladdr(frames[i],&info) && info.dli_sname &&
           (strstr(info.dli_sname,"handle_execute") || strstr(info.dli_sname,"execute_forward"))) return 1;
    }
    return 0;
}
extern bool __real_fb_guc_cache_insert_or_update(fb_guc_cache_t*,const char*,size_t,const char*,size_t,uint64_t,int,fb_guc_value_source_t);
bool __wrap_fb_guc_cache_insert_or_update(fb_guc_cache_t *c,const char *k,size_t kl,const char *v,size_t vl,uint64_t hash,int idx,fb_guc_value_source_t src) {
    char key[128],value[512];snprintf(key,sizeof(key),"%.*s",(int)kl,k);snprintf(value,sizeof(value),"%.*s",(int)vl,v);
    event("cache_before",c,key,value,1);
    if(!strcmp(key,"work_mem") && c->cache_name &&
       ((!strcmp(c->cache_name,"frontend") && fail("apply")) ||
        (!strcmp(c->cache_name,"transaction") && fail("pre_record")))) return false;
    bool rc=__real_fb_guc_cache_insert_or_update(c,k,kl,v,vl,hash,idx,src);
    event("cache_after",c,key,value,rc);return rc;
}
extern bool __real_fb_guc_cache_copy(fb_guc_cache_t*,const fb_guc_cache_t*);
bool __wrap_fb_guc_cache_copy(fb_guc_cache_t *dst,const fb_guc_cache_t *src) {
    char *value=NULL;size_t len=0;char v[512]="";
    if(src && src->map && fb_guc_cache_find((fb_guc_cache_t*)src,"work_mem",8,&value,&len)) snprintf(v,sizeof(v),"%.*s",(int)len,value);
    bool rc=__real_fb_guc_cache_copy(dst,src);event("cache_copy",dst,"work_mem",v,rc);return rc;
}
extern void __real_fb_guc_cache_clear(fb_guc_cache_t*);
void __wrap_fb_guc_cache_clear(fb_guc_cache_t *c) {event("cache_clear",c,"","",1);__real_fb_guc_cache_clear(c);}
extern void __real_fb_guc_cache_free(fb_guc_cache_t*);
void __wrap_fb_guc_cache_free(fb_guc_cache_t *c) {event("cache_free",c,"","",1);__real_fb_guc_cache_free(c);}
extern od_frontend_status_t __real_fb_add_outstanding_request(od_server_t*,char,od_hashmap_list_item_t*,od_hash_t,fb_internal_request_kind_t,fb_global_ps_entry_t*,fb_outstanding_request_t**);
od_frontend_status_t __wrap_fb_add_outstanding_request(od_server_t *s,char type,od_hashmap_list_item_t *item,od_hash_t hash,fb_internal_request_kind_t internal,fb_global_ps_entry_t *entry,fb_outstanding_request_t **out) {
    char kind[2]={type,0};event("outstanding",NULL,kind,"",internal);
    if(internal==FB_INTERNAL_REQ_NONE && (type=='E' || type=='Q') && fail("outstanding")) return OD_EOOM;
    return __real_fb_add_outstanding_request(s,type,item,hash,internal,entry,out);
}
extern machine_msg_t *__real_machine_msg_create(int);
machine_msg_t *__wrap_machine_msg_create(int size) {
    if(execute_stack() && fail("response_construct")) return NULL;
    return __real_machine_msg_create(size);
}
static int set_complete(void *data,int size) {
    unsigned char *p=data;return size>=9 && p[0]=='C' &&
        (!memcmp(p+5,"SET\0",4) || (size>=11 && !memcmp(p+5,"RESET\0",6)));
}
extern int __real_machine_iov_add(machine_iov_t*,machine_msg_t*);
int __wrap_machine_iov_add(machine_iov_t *iov,machine_msg_t *msg) {
    if(set_complete(machine_msg_data(msg),machine_msg_size(msg))) {
        event("response_queue",NULL,"SET/RESET","",1);
        if(fail("response_queue")) return -1;
    }
    return __real_machine_iov_add(iov,msg);
}
extern int __real_machine_iov_add_pointer(machine_iov_t*,void*,int);
int __wrap_machine_iov_add_pointer(machine_iov_t *iov,void *data,int size) {
    if(set_complete(data,size)) {
        event("response_queue",NULL,"SET/RESET","",1);
        if(fail("response_queue")) return -1;
    }
    return __real_machine_iov_add_pointer(iov,data,size);
}
extern int __real_machine_msg_write(machine_msg_t*,void*,int);
int __wrap_machine_msg_write(machine_msg_t *msg,void *buf,int size) {
    unsigned char *p=buf;
    if(p && size>5 && ((p[0]=='E' && p[5]==0) || (p[0]=='Q' && size>17 && !memcmp(p+5,"SET work_mem",12))) && fail("forward")) return -1;
    return __real_machine_msg_write(msg,buf,size);
}

extern ssize_t __real_mm_io_write(void *,void *,size_t);
ssize_t __wrap_mm_io_write(void *io,void *buf,size_t size) {
    unsigned char *p=buf;
    if(p && size>5 && ((p[0]=='E' && p[5]==0) ||
       (p[0]=='Q' && size>17 && !memcmp(p+5,"SET work_mem",12))) && fail("forward")) return -1;
    return __real_mm_io_write(io,buf,size);
}

#include <sys/uio.h>
#include "parser/fb_sql_parse_state.h"
extern int __real_mm_socket_writev(int,struct iovec*,int);
int __wrap_mm_socket_writev(int fd,struct iovec *iov,int count) {
    for(int i=0;i<count;i++) {
        unsigned char *p=iov[i].iov_base;size_t size=iov[i].iov_len;
        if(p && size>5 && ((p[0]=='E' && p[5]==0) ||
           (p[0]=='Q' && size>17 && !memcmp(p+5,"SET work_mem",12))) && fail("forward")) {
            errno=EIO;return -1;
        }
    }
    return __real_mm_socket_writev(fd,iov,count);
}
extern bool __real_fb_sql_parse_client_state_prepare_forbidden(fb_sql_parse_client_state_t*,fb_sql_parse_pending_forbidden_queue_t*,const fb_sql_stmt_ast_t*,bool);
bool __wrap_fb_sql_parse_client_state_prepare_forbidden(fb_sql_parse_client_state_t *state,fb_sql_parse_pending_forbidden_queue_t *queue,const fb_sql_stmt_ast_t *ast,bool simple) {
    event("pending_prepare",NULL,simple?"Q":"E","",1);
    if(fail("pending")) return false;
    return __real_fb_sql_parse_client_state_prepare_forbidden(state,queue,ast,simple);
}

extern int __real_od_reset(od_server_t*);
int __wrap_od_reset(od_server_t *server) {
    event("backend_reset",&server->guc_cache,"","",1);
    return __real_od_reset(server);
}
