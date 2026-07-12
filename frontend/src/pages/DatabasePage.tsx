import { useState, useEffect, useMemo } from 'react'
import { useAuthStore } from '../stores/authStore'
import Pagination from '../components/Pagination'

interface DBItem {
  id: number
  name: string
  description: string | null
  note: string | null
  table_count: number
  created_by_name: string | null
  updated_by_name: string | null
  created_at: string
  updated_at: string | null
}

export default function DatabasePage() {
  const token = useAuthStore((s) => s.token)
  const user = useAuthStore((s) => s.user)
  const [dbs, setDbs] = useState<DBItem[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [editId, setEditId] = useState<number | null>(null)
  const [editNote, setEditNote] = useState('')
  const [delDb, setDelDb] = useState<DBItem | null>(null)
  const [delPreview, setDelPreview] = useState<any>(null)
  const [delConfirm, setDelConfirm] = useState('')
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [pageSize, setPageSize] = useState(20)

  const headers = useMemo(() => ({
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  }), [token])

  const load = async (p?: number, ps?: number) => {
    const pg = p ?? page
    const sz = ps ?? pageSize
    const res = await fetch(`/api/v1/databases?page=${pg}&page_size=${sz}`, { headers })
    const json = await res.json()
    if (json.code === 0) {
      setDbs(json.data.items)
      setTotal(json.data.total)
      setPage(json.data.page)
      if (ps) setPageSize(sz)
    }
  }

  useEffect(() => { load() }, [])

  const handleCreate = async () => {
    if (!name.trim()) return
    await fetch('/api/v1/databases', {
      method: 'POST', headers,
      body: JSON.stringify({ name, description: desc || null }),
    })
    setName(''); setDesc(''); setShowCreate(false); load()
  }

  const handleSaveNote = async (id: number) => {
    await fetch(`/api/v1/databases/${id}`, {
      method: 'PUT', headers,
      body: JSON.stringify({ note: editNote }),
    })
    setEditId(null); load()
  }

  const handlePreviewDelete = async (db: DBItem) => {
    setDelDb(db)
    const res = await fetch(`/api/v1/databases/${db.id}/preview-delete`, { headers })
    const json = await res.json()
    if (json.code === 0) setDelPreview(json.data)
  }

  const handleDelete = async () => {
    if (!delDb) return
    const res = await fetch(`/api/v1/databases/${delDb.id}?confirm_name=${encodeURIComponent(delConfirm)}`, {
      method: 'DELETE', headers,
    })
    if (res.ok) { setDelDb(null); setDelConfirm(''); setDelPreview(null); load() }
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-gray-800">数据库管理</h1>
        {user?.role === 'admin' && (
          <button onClick={() => setShowCreate(!showCreate)} className="bg-blue-600 text-white text-sm px-3 py-1.5 rounded">
            + 新建库
          </button>
        )}
      </div>

      {showCreate && (
        <div className="mb-4 p-4 bg-gray-50 rounded border">
          <input className="border rounded px-3 py-1.5 text-sm w-48 mr-2" placeholder="库名" value={name} onChange={(e) => setName(e.target.value)} />
          <input className="border rounded px-3 py-1.5 text-sm w-48 mr-2" placeholder="描述(可选)" value={desc} onChange={(e) => setDesc(e.target.value)} />
          <button onClick={handleCreate} className="bg-green-600 text-white text-sm px-3 py-1 rounded">创建</button>
        </div>
      )}

      <div className="flex gap-2 mb-4">
        <input
          className="border rounded px-3 py-1.5 text-sm w-48"
          placeholder="搜索库名"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-gray-500">
            <th className="py-2">库名</th>
            <th>表数</th>
            <th>备注</th>
            <th>创建人</th>
            <th>创建时间</th>
            <th>修改人</th>
            <th>修改时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {dbs.filter((db) => !keyword || db.name.includes(keyword)).map((db) => (
            <tr key={db.id} className="border-b hover:bg-gray-50">
              <td className="py-2 font-medium">{db.name}</td>
              <td className="text-gray-600">{db.table_count}</td>
              <td>
                {editId === db.id ? (
                  <div className="flex gap-1">
                    <input className="border rounded px-2 py-0.5 text-xs w-32" value={editNote} onChange={(e) => setEditNote(e.target.value)} />
                    <button onClick={() => handleSaveNote(db.id)} className="text-xs text-green-600">保存</button>
                    <button onClick={() => setEditId(null)} className="text-xs text-gray-400">取消</button>
                  </div>
                ) : (
                  <span className="text-gray-500 text-xs cursor-pointer" onClick={() => { setEditId(db.id); setEditNote(db.note || '') }}>
                    {db.note || '(点击编辑)'}
                  </span>
                )}
              </td>
              <td className="text-gray-500 text-xs">{db.created_by_name || '-'}</td>
              <td className="text-gray-400 text-xs">{db.created_at?.slice(0, 10)}</td>
              <td className="text-gray-500 text-xs">{db.updated_by_name || '-'}</td>
              <td className="text-gray-400 text-xs">{db.updated_at?.slice(0, 16) || '-'}</td>
              <td>
                {user?.role === 'admin' && (
                  <button onClick={() => handlePreviewDelete(db)} className="text-xs text-red-500 hover:underline">删除</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Pagination
        page={page} pageSize={pageSize} total={total}
        onChange={(p) => load(p)}
        onSizeChange={(s) => load(1, s)}
      />

      {/* 删除确认弹窗 */}
      {delDb && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setDelDb(null)}>
          <div className="bg-white rounded-lg p-4 w-96" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-medium text-sm text-red-600 mb-2">删除数据库: {delDb.name}</h3>
            {delPreview && (
              <div className="text-xs text-gray-600 mb-3 space-y-1">
                <div>⚠ 将级联删除:</div>
                <div>· {delPreview.tables} 张表</div>
                <div>· {delPreview.fields} 个字段</div>
                <div>· 所有关联的血缘数据</div>
              </div>
            )}
            <input
              className="border rounded px-3 py-1.5 text-sm w-full mb-2"
              placeholder={`输入 "${delDb.name}" 确认删除`}
              value={delConfirm}
              onChange={(e) => setDelConfirm(e.target.value)}
            />
            <div className="flex gap-2">
              <button onClick={() => setDelDb(null)} className="text-sm border rounded px-4 py-1">取消</button>
              <button
                onClick={handleDelete}
                disabled={delConfirm !== delDb.name}
                className="text-sm bg-red-600 text-white rounded px-4 py-1 disabled:opacity-50"
              >
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
