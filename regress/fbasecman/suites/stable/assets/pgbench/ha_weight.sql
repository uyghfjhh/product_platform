SET NODE WEIGHT (pg_241=11,{{mmr_host}}:{{mmr1_standby3_port}}=12);
SET NODE WEIGHT (pg_241=10,{{mmr_host}}:{{mmr1_standby3_port}}=10);
SET NODE WEIGHT (pg_240=7,{{mmr_host}}:{{mmr1_standby2_port}}=13,pg_250=9);
SET NODE WEIGHT (pg_240=10,{{mmr_host}}:{{mmr1_standby2_port}}=10,pg_250=10);
SET NODE WEIGHT ({{mmr_host}}:{{mmr1_standby1_port}}=3,{{mmr_host}}:{{mmr1_standby2_port}}=17,{{mmr_host}}:{{mmr2_standby1_port}}=5,{{mmr_host}}:{{mmr2_standby2_port}}=15);
SET NODE WEIGHT ({{mmr_host}}:{{mmr1_standby1_port}}=10,{{mmr_host}}:{{mmr1_standby2_port}}=10,{{mmr_host}}:{{mmr2_standby1_port}}=10,{{mmr_host}}:{{mmr2_standby2_port}}=10);
\sleep 1 s
