import { useMemo } from 'react';
import { createStudioBFFClient } from '@prisma/studio-core/data/bff';
import { createPostgresAdapter } from '@prisma/studio-core/data/postgres-core';
import { Studio } from '@prisma/studio-core/ui';
import '@prisma/studio-core/ui/index.css';

/**
 * Prisma Studio 嵌入式数据管理器——经平台 BFF 端点连环境登记的数据库，
 * 连接/认证与端口选择沿用环境模型，不维护第二套连接配置。
 */
export default function StudioPanel({ environmentId, port }: { environmentId: string; port: number }) {
  const adapter = useMemo(() => {
    const client = createStudioBFFClient({
      url: `/api/v1/environments/${encodeURIComponent(environmentId)}/studio?port=${port}`,
      customPayload: { environment_id: environmentId, port },
    });
    return createPostgresAdapter({ executor: client });
  }, [environmentId, port]);
  return <Studio adapter={adapter} />;
}
