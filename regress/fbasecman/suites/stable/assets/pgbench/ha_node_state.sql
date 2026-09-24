SET NODE PARTED (pg_240,{{mmr_host}}:{{mmr1_standby2_port}});
SET NODE ACTIVE (pg_240,{{mmr_host}}:{{mmr1_standby2_port}});
SET NODE PARTED {{mmr_host}}:{{mmr1_standby3_port}};
SET NODE ACTIVE {{mmr_host}}:{{mmr1_standby3_port}};
SET NODE PARTED (pg_241,{{mmr_host}}:{{mmr1_standby1_port}});
SET NODE ACTIVE (pg_241,{{mmr_host}}:{{mmr1_standby1_port}});
SET NODE PARTED (pg_250,{{mmr_host}}:{{mmr2_standby1_port}},pg_251);
SET NODE ACTIVE (pg_250,{{mmr_host}}:{{mmr2_standby1_port}},pg_251);
SET NODE PARTED ({{mmr_host}}:{{mmr1_standby1_port}},{{mmr_host}}:{{mmr2_standby2_port}});
SET NODE ACTIVE ({{mmr_host}}:{{mmr1_standby1_port}},{{mmr_host}}:{{mmr2_standby2_port}});
\sleep 1 s
