import { useState, useEffect, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

interface TableItem {
  id: number
  table_name: string
  schema_name: string
  table_type: string
  source: string
  col_count: number
  created_at: string
}

export default function TableListPage() {
  const token = useAuthStore((s) => s.token)
  const [tables, setTables] = useState<TableItem[]>([])
  const [total, setTotal] = useState(0)
  const [ddl, setDdl] = useState('')
  const [source, setSource] = useState('root')
  const [showCreate, setShowCreate] = useState(false)
  const [status, setStatus] = useState('')

  const headers = useMemo(() => ({
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  }), [token])

  const loadTables = async () => {
    const res = await fetch('/api/v1/tables?page=1&page_size=50', { headers })
    const json = await res.json()
    if (json.code === 0) {
      setTables(json.data.items)
      setTotal(json.data.total)
    }
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
                <option value="middle">middle</option>
              </select>
            </label>
            <button onClick={handleCreate} className="bg-green-600 text-white text-xs px-3 py-1 rounded">
              解析并保存
            </button>
            {status && <span className="text-xs text-gray-500">{status}</span>}
          </div>
        </div>
      )}

      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-gray-500">
            <th className="py-2">表名</th>
            <th>类型</th>
            <th>来源</th>
            <th>列数</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          {tables.map((t) => (
            <tr key={t.id} className="border-b hover:bg-gray-50">
              <td className="py-2">
                <Link to={`/tables/${t.id}`} className="text-blue-600 hover:underline font-mono text-xs">
                  {t.table_name}
                </Link>
              </td>
              <td className="text-gray-600">{t.table_type}</td>
              <td>
                <span className={`text-xs px-1.5 py-0.5 rounded ${t.source === 'root' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'}`}>
                  {t.source}
                </span>
              </td>
              <td className="text-gray-600">{t.col_count}</td>
              <td className="text-gray-400 text-xs">{t.created_at?.slice(0, 10)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
