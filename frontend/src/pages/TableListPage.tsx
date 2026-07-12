import { useState, useEffect, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import Pagination from '../components/Pagination'

interface TableItem {
  id: number
  table_name: string
  db_name: string
  table_type: string
  source: string
  col_count: number
  created_by_name: string | null
  updated_by_name: string | null
  created_at: string
  updated_at: string | null
}

export default function TableListPage() {
  const token = useAuthStore((s) => s.token)
  const [tables, setTables] = useState<TableItem[]>([])
  const [total, setTotal] = useState(0)
  const [ddl, setDdl] = useState('')
  const [source, setSource] = useState('root')
  const [showCreate, setShowCreate] = useState(false)
  const [status, setStatus] = useState('')
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [upgradeTable, setUpgradeTable] = useState<TableItem | null>(null)
  const [upgradeSql, setUpgradeSql] = useState('')
  const [upgradePreview, setUpgradePreview] = useState<any>(null)
  const [delTable, setDelTable] = useState<TableItem | null>(null)
  const [delPreview, setDelPreview] = useState<any>(null)
  const user = useAuthStore((s) => s.user)

  const headers = useMemo(() => ({
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  }), [token])

  const loadTables = async (p?: number, kw?: string, ps?: number) => {
    const pg = p ?? page
    const k = kw !== undefined ? kw : keyword
    const sz = ps ?? pageSize
    let url = `/api/v1/tables?page=${pg}&page_size=${sz}`
    if (k) url += '&keyword=' + encodeURIComponent(k)
    const res = await fetch(url, { headers })
    const json = await res.json()
    if (json.code === 0) {
      setTables(json.data.items)
      setTotal(json.data.total)
      setPage(json.data.page)
      if (ps) setPageSize(sz)
    }
  }

  const handleSearch = () => { setPage(1); loadTables(1) }

  const handleUpgradePreview = async (t: TableItem) => {
    setUpgradeTable(t)
    const res = await fetch(`/api/v1/lineage/upgrade/preview/${t.table_name}`, { headers })
    const json = await res.json()
    if (json.code === 0) setUpgradePreview(json.data)
  }

  const handleUpgradeExecute = async () => {
    if (!upgradeTable) return
    const res = await fetch('/api/v1/lineage/upgrade/execute', {
      method: 'POST', headers,
      body: JSON.stringify({ table_name: upgradeTable.table_name, extended_sql: upgradeSql }),
    })
    const json = await res.json()
    if (json.code === 0) {
      setUpgradeTable(null); setUpgradeSql(''); setUpgradePreview(null); loadTables()
    }
  }

  const handleDowngrade = async (t: TableItem) => {
    if (!confirm(`确认将 ${t.table_name} 从 sink 降级为 root？`)) return
    await fetch(`/api/v1/tables/${t.id}/downgrade`, { method: 'POST', headers })
    loadTables()
  }

  const handleDelPreview = async (t: TableItem) => {
    setDelTable(t)
    const res = await fetch(`/api/v1/tables/${t.id}/preview-delete`, { headers })
    const json = await res.json()
    if (json.code === 0) setDelPreview(json.data)
  }

  const handleDelete = async () => {
    if (!delTable) return
    await fetch(`/api/v1/tables/${delTable.id}`, { method: 'DELETE', headers })
    setDelTable(null); setDelPreview(null); loadTables()
  }

  const handleChangeSink = async (t: TableItem) => {
    setUpgradeTable(t)
    const res = await fetch(`/api/v1/lineage/upgrade/preview/${t.table_name}`, { headers })
    const json = await res.json()
    if (json.code === 0) setUpgradePreview(json.data)
  }

  useEffect(() => { loadTables() }, [])

  const handleCreate = async () => {
    if (!ddl.trim()) return
    setStatus('creating')
    const res = await fetch('/api/v1/tables', {
      method: 'POST',
      headers,
      body: JSON.stringify({ ddl, source }),
    })
    const json = await res.json()
    if (json.code === 0) {
      setStatus('创建成功')
      setDdl('')
      setShowCreate(false)
      loadTables()
    } else {
      setStatus(json.message)
    }
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-gray-800">表结构管理 ({total})</h1>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="bg-blue-600 text-white text-sm px-3 py-1.5 rounded hover:bg-blue-700"
        >
          + 新建表
        </button>
      </div>

      {showCreate && (
        <div className="mb-4 p-4 bg-gray-50 rounded border">
          <textarea
            className="w-full border rounded px-3 py-2 text-xs font-mono h-24"
            placeholder={`CREATE TABLE my_table (\n  col1 STRING COMMENT '列1',\n  col2 INT COMMENT '列2 ENUM(1,2,3)'\n)`}
            value={ddl}
            onChange={(e) => setDdl(e.target.value)}
          />
          <div className="flex items-center gap-3 mt-2">
            <label className="text-xs text-gray-600">
              来源:
              <select
                value={source}
                onChange={(e) => setSource(e.target.value)}
                className="ml-1 border rounded px-2 py-0.5 text-xs"
              >
                <option value="root">root</option>
                <option value="sink">sink</option>
                <option value="view">view</option>
              </select>
            </label>
            <button onClick={handleCreate} className="bg-green-600 text-white text-xs px-3 py-1 rounded">
              解析并保存
            </button>
            {status && <span className="text-xs text-gray-500">{status}</span>}
          </div>
        </div>
      )}

      <div className="flex gap-2 mb-4">
        <input
          className="border rounded px-3 py-1.5 text-sm w-64"
          placeholder="搜索表名/库名"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
        />
        <button onClick={handleSearch} className="text-sm border rounded px-3 py-1 hover:bg-gray-50">搜索</button>
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-gray-500">
            <th className="py-2">库名</th>
            <th>表名</th>
            <th>类型</th>
            <th>来源</th>
            <th>列数</th>
            <th>创建人</th>
            <th>创建时间</th>
            <th>修改人</th>
            <th>修改时间</th>
            {user?.role === 'admin' && <th>操作</th>}
          </tr>
        </thead>
        <tbody>
          {tables.map((t) => (
            <tr key={t.id} className="border-b hover:bg-gray-50">
              <td className="py-1 text-xs text-gray-500">{t.db_name}</td>
              <td className="py-1">
                <Link to={`/tables/${t.id}`} className="text-blue-600 hover:underline font-mono text-xs">
                  {t.table_name}
                </Link>
              </td>
              <td className="text-gray-600 text-xs">{t.table_type}</td>
              <td>
                <span className={`text-xs px-1.5 py-0.5 rounded ${t.source === 'root' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'}`}>
                  {t.source}
                </span>
              </td>
              <td className="text-gray-600 text-xs">{t.col_count}</td>
              <td className="text-gray-500 text-xs">{t.created_by_name || '-'}</td>
              <td className="text-gray-400 text-xs">{t.created_at?.slice(0, 10)}</td>
              <td className="text-gray-500 text-xs">{t.updated_by_name || '-'}</td>
              <td className="text-gray-400 text-xs">{t.updated_at?.slice(0, 16) || '-'}</td>
              <td className="text-xs space-x-1">
                {user?.role === 'admin' && (
                  <>
                    {t.source === 'root' && (
                      <button onClick={() => handleUpgradePreview(t)} className="text-blue-500 hover:underline">升级</button>
                    )}
                    {(t.source === 'sink' || t.source === 'view') && (
                      <>
                        <button onClick={() => handleChangeSink(t)} className="text-blue-500 hover:underline">变更</button>
                        {t.source === 'sink' && (
                          <button onClick={() => handleDowngrade(t)} className="text-orange-500 hover:underline">降级</button>
                        )}
                      </>
                    )}
                    <button onClick={() => handleDelPreview(t)} className="text-red-500 hover:underline">删除</button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Pagination
        page={page} pageSize={pageSize} total={total}
        onChange={(p) => loadTables(p)}
        onSizeChange={(s) => loadTables(1, undefined, s)}
      />

      {/* 升级/变更弹窗 */}
      {upgradeTable && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => { setUpgradeTable(null); setUpgradePreview(null); setUpgradeSql('') }}>
          <div className="bg-white rounded-lg p-4 w-[500px] max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-medium text-sm mb-2">
              {upgradeTable.source === 'root' ? `升级 ${upgradeTable.table_name}: root → sink` : `变更 ${upgradeTable.table_name} 的 sink SQL`}
            </h3>
            {upgradePreview && (
              <div className="text-xs text-gray-600 mb-3 grid grid-cols-2 gap-2">
                <div className="bg-gray-50 p-2 rounded">字段: {upgradePreview.field_count}</div>
                <div className="bg-gray-50 p-2 rounded">下游节点: {upgradePreview.total_affected_nodes}</div>
                <div className="bg-gray-50 p-2 rounded">受影响查询: {upgradePreview.affected_queries}</div>
              </div>
            )}
            <textarea
              className="w-full border rounded px-3 py-2 text-xs font-mono h-32"
              placeholder="SELECT col1, col2 FROM ancestor ..."
              value={upgradeSql}
              onChange={(e) => setUpgradeSql(e.target.value)}
            />
            <div className="flex gap-2 mt-3">
              <button onClick={() => { setUpgradeTable(null); setUpgradePreview(null); setUpgradeSql('') }} className="text-sm border rounded px-4 py-1">取消</button>
              <button onClick={handleUpgradeExecute} disabled={!upgradeSql.trim()} className="text-sm bg-red-600 text-white rounded px-4 py-1 disabled:opacity-50">
                {upgradeTable.source === 'root' ? '确认升级' : '确认变更'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 删除预览弹窗 */}
      {delTable && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => { setDelTable(null); setDelPreview(null) }}>
          <div className="bg-white rounded-lg p-4 w-96" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-medium text-sm text-red-600 mb-2">删除表: {delTable.table_name}</h3>
            {delPreview && (
              <div className="text-xs text-gray-600 mb-3">
                <div>⚠ 将删除 {delPreview.field_count} 个关联字段</div>
              </div>
            )}
            <div className="flex gap-2">
              <button onClick={() => { setDelTable(null); setDelPreview(null) }} className="text-sm border rounded px-4 py-1">取消</button>
              <button onClick={handleDelete} className="text-sm bg-red-600 text-white rounded px-4 py-1">确认删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
