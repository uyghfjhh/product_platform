SET NODE WRITE {{mmr_host}}:{{mmr2_port}} IN GROUP mmrhint;
SET NODE PROMOTED pg_220 IN GROUP mmrhint;
SET NODE WRITE pg_230 IN GROUPS (mmrhint,mmrport);
SET NODE PROMOTED {{mmr_host}}:{{mmr1_port}} IN GROUPS (mmrhint,mmrport);
SET NODE WRITE {{mmr_host}}:{{mmr1_port}} IN GROUPS (mmrhint_b,mmrport_b);
SET NODE PROMOTED pg_230 IN GROUPS (mmrhint_b,mmrport_b);
SET NODE WRITE {{mmr_host}}:{{mmr2_port}} IN GROUPS (mmrhint,mmrhint_b,mmrport,mmrport_b);
SET NODE PROMOTED {{mmr_host}}:{{mmr1_port}} IN GROUPS (mmrhint,mmrhint_b,mmrport,mmrport_b);
SET NODE WRITE pg_220 IN GROUPS (mmrhint,mmrport);
SET NODE PROMOTED pg_230 IN GROUPS (mmrhint,mmrport);
SET NODE WRITE {{mmr_host}}:{{mmr1_port}} IN GROUPS (mmrhint,mmrhint_b,mmrport,mmrport_b);
SET NODE PROMOTED {{mmr_host}}:{{mmr2_port}} IN GROUPS (mmrhint,mmrhint_b,mmrport,mmrport_b);
\sleep 1 s
