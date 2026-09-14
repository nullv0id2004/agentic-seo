import { Pool, PoolClient } from "pg";

// One pool. Every query runs inside withProject(), which pins app.project_id for RLS and adds the
// explicit project_id filter the worker also uses. There is no unscoped query helper on purpose.
let pool: Pool | null = null;

function getPool(): Pool {
  if (!pool) {
    const url = process.env.CONSOLE_DATABASE_URL;
    if (!url) throw new Error("CONSOLE_DATABASE_URL is not set");
    pool = new Pool({ connectionString: url, max: 4 });
  }
  return pool;
}

export type Row = Record<string, unknown>;

export async function listProjects(): Promise<Row[]> {
  const client = await getPool().connect();
  try {
    const r = await client.query("select * from list_active_projects()");
    return r.rows;
  } finally {
    client.release();
  }
}

export async function projectBySlug(slug: string): Promise<Row | null> {
  const projects = await listProjects();
  return projects.find((p) => p.slug === slug) ?? null;
}

export async function withProject<T>(projectId: string, fn: (q: (sql: string, params?: unknown[]) => Promise<Row[]>) => Promise<T>): Promise<T> {
  const client: PoolClient = await getPool().connect();
  try {
    await client.query("begin");
    await client.query("select set_config('app.project_id', $1, true)", [projectId]);
    const q = async (sql: string, params: unknown[] = []) => {
      if (!/project_id/.test(sql)) throw new Error("console query without project_id filter refused");
      const r = await client.query(sql, [projectId, ...params]);
      return r.rows as Row[];
    };
    const out = await fn(q);
    await client.query("commit");
    return out;
  } catch (e) {
    await client.query("rollback");
    throw e;
  } finally {
    client.release();
  }
}
