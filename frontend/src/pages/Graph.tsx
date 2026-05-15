import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { RefreshCw, ZoomIn, ZoomOut, Maximize2, BookOpen, Trash2 } from 'lucide-react'
import { getWikiGraph, getWikiPage, deleteWikiPage, type GraphData, type WikiPageDetail } from '../api/client'

const PAGE_TYPE_COLOR: Record<string, string> = {
  index:   '#8b5cf6',
  summary: '#3b82f6',
  entity:  '#10b981',
  concept: '#f59e0b',
}

const PAGE_TYPE_LABEL: Record<string, string> = {
  index:   '索引',
  summary: '摘要',
  entity:  '實體',
  concept: '概念',
}

interface GraphNode {
  id: string
  title: string
  slug: string
  page_type: string
  // force-graph 會注入的欄位
  x?: number
  y?: number
  vx?: number
  vy?: number
  fx?: number
  fy?: number
}

interface GraphLink {
  source: string | GraphNode
  target: string | GraphNode
  link_text?: string | null
}

export default function GraphPage() {
  const [graphData, setGraphData] = useState<{ nodes: GraphNode[]; links: GraphLink[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedPage, setSelectedPage] = useState<WikiPageDetail | null>(null)
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null)
  const fgRef = useRef<any>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data: GraphData = await getWikiGraph()
      setGraphData({
        nodes: data.nodes as GraphNode[],
        links: data.edges.map((e) => ({
          source: e.source,
          target: e.target,
          link_text: e.link_text,
        })),
      })
    } catch {
      setError('載入失敗，請確認 API Key')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleNodeClick = useCallback(async (node: GraphNode) => {
    try {
      setSelectedPage(await getWikiPage(node.id))
    } catch {}
  }, [])

  const handleZoomIn = () => fgRef.current?.zoom(1.5, 400)
  const handleZoomOut = () => fgRef.current?.zoom(0.7, 400)
  const handleFit = () => fgRef.current?.zoomToFit(400, 40)

  // 物理參數調強：節點之間排斥力大、連線距離長 → 自然散開，
  // 減少標籤重疊。每次 graphData 變動都套用。
  useEffect(() => {
    if (!graphData || !fgRef.current) return
    const charge = fgRef.current.d3Force('charge')
    if (charge) charge.strength(-220).distanceMax(600)
    const link = fgRef.current.d3Force('link')
    if (link) link.distance(80)
    fgRef.current.d3ReheatSimulation?.()
  }, [graphData])

  // Hover focus：滑到節點時取出 1-hop 鄰居 id 集合，
  // render 階段用來決定哪些 node / link 維持正常色、哪些淡出。
  const linkEndpointId = (e: string | GraphNode): string =>
    typeof e === 'string' ? e : e.id

  const neighborIds = useMemo(() => {
    if (!hoveredNode || !graphData) return new Set<string>()
    const ids = new Set<string>()
    for (const l of graphData.links) {
      const s = linkEndpointId(l.source)
      const t = linkEndpointId(l.target)
      if (s === hoveredNode.id) ids.add(t)
      else if (t === hoveredNode.id) ids.add(s)
    }
    return ids
  }, [hoveredNode, graphData])

  const nodeCanvasObject = useCallback((node: GraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const label = node.title
    const isIndex = node.page_type === 'index'
    // index 頁是 query 路由的樞紐，畫得明顯大、文字也大
    const baseR = isIndex ? 16 : 10
    const baseFont = isIndex ? 15 : 13
    const fontSize = Math.max(isIndex ? 12 : 10, baseFont / globalScale)
    const r = Math.max(isIndex ? 10 : 6, baseR / Math.sqrt(globalScale))
    const color = PAGE_TYPE_COLOR[node.page_type] || '#6b7280'
    const isHovered = hoveredNode?.id === node.id
    const isNeighbor = neighborIds.has(node.id)
    const isFocused = isHovered || isNeighbor
    // hover 模式下非聚焦節點淡出（含 index）；非 hover 模式人人正常色
    const alpha = hoveredNode && !isFocused ? 0.15 : 1

    // 節點圓圈
    ctx.globalAlpha = alpha
    ctx.beginPath()
    ctx.arc(node.x!, node.y!, r + (isHovered ? 3 : 0), 0, 2 * Math.PI)
    ctx.fillStyle = color
    ctx.fill()

    // 外框：index 頁加粗強調樞紐地位
    ctx.beginPath()
    ctx.arc(node.x!, node.y!, r + (isHovered ? 3 : 0), 0, 2 * Math.PI)
    ctx.strokeStyle = isHovered ? '#fff' : isIndex ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.5)'
    ctx.lineWidth = (isHovered ? 2.5 : isIndex ? 2.5 : 1.5) / globalScale
    ctx.stroke()

    // 標籤策略：減少視覺擁擠
    // - index 永遠顯示（路由樞紐當地圖座標）
    // - 在 hover focus 模式：hovered 與其鄰居都顯示
    // - zoom 大於 1.5：所有節點顯示（使用者已主動放大）
    // - 其他情況：非 index 不顯示，避免密密麻麻
    const shouldShowLabel =
      isIndex ||
      isHovered ||
      isNeighbor ||
      globalScale > 1.5
    if (shouldShowLabel) {
      ctx.font = `${isIndex || isHovered ? 'bold ' : ''}${fontSize}px sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      const textY = node.y! + r + 3 / globalScale
      const textWidth = ctx.measureText(label).width
      ctx.fillStyle = 'rgba(255,255,255,0.85)'
      ctx.fillRect(node.x! - textWidth / 2 - 2, textY - 1, textWidth + 4, fontSize + 2)
      ctx.fillStyle = isHovered ? '#1d4ed8' : isIndex ? '#6b21a8' : '#374151'
      ctx.fillText(label, node.x!, textY)
    }
    ctx.globalAlpha = 1
  }, [hoveredNode, neighborIds])

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Graph 主區域 */}
      <div className="flex-1 relative bg-gray-50">
        {/* 工具列 */}
        <div className="absolute top-4 left-4 z-10 flex items-center gap-2">
          <div className="bg-white rounded-xl shadow border border-gray-200 px-4 py-2 flex items-center gap-3">
            <span className="font-semibold text-gray-700 text-sm">Wiki 知識圖譜</span>
            {graphData && (
              <span className="text-xs text-gray-400">
                {graphData.nodes.length} 頁 · {graphData.links.length} 連結
              </span>
            )}
          </div>
          <button
            onClick={load}
            className="bg-white rounded-xl shadow border border-gray-200 p-2 hover:bg-gray-50"
            title="重新載入"
          >
            <RefreshCw size={15} className={loading ? 'animate-spin text-blue-500' : 'text-gray-500'} />
          </button>
        </div>

        {/* 縮放控制 */}
        <div className="absolute top-4 right-4 z-10 flex flex-col gap-1">
          <button onClick={handleZoomIn}  className="bg-white rounded-lg shadow border border-gray-200 p-2 hover:bg-gray-50"><ZoomIn  size={15} className="text-gray-600" /></button>
          <button onClick={handleZoomOut} className="bg-white rounded-lg shadow border border-gray-200 p-2 hover:bg-gray-50"><ZoomOut size={15} className="text-gray-600" /></button>
          <button onClick={handleFit}     className="bg-white rounded-lg shadow border border-gray-200 p-2 hover:bg-gray-50"><Maximize2 size={15} className="text-gray-600" /></button>
        </div>

        {/* 圖例 */}
        <div className="absolute bottom-4 left-4 z-10 bg-white rounded-xl shadow border border-gray-200 px-3 py-2">
          <p className="text-xs text-gray-400 mb-1.5 font-medium">頁面類型</p>
          <div className="flex flex-col gap-1">
            {Object.entries(PAGE_TYPE_COLOR).map(([type, color]) => (
              <div key={type} className="flex items-center gap-2">
                <span
                  className="rounded-full flex-shrink-0"
                  style={{
                    backgroundColor: color,
                    width: type === 'index' ? '14px' : '10px',
                    height: type === 'index' ? '14px' : '10px',
                    border: type === 'index' ? '1.5px solid #fff' : 'none',
                    boxShadow: type === 'index' ? '0 0 0 1px rgba(0,0,0,0.1)' : 'none',
                  }}
                />
                <span className="text-xs text-gray-600">
                  {PAGE_TYPE_LABEL[type] || type}
                  {type === 'index' && <span className="text-gray-400"> · 路由樞紐</span>}
                </span>
              </div>
            ))}
          </div>
        </div>

        {loading && (
          <div className="absolute inset-0 flex items-center justify-center z-20 bg-gray-50/80">
            <RefreshCw size={28} className="animate-spin text-blue-400" />
          </div>
        )}

        {error && (
          <div className="absolute inset-0 flex items-center justify-center z-20">
            <p className="text-red-500 bg-white px-4 py-3 rounded-lg shadow">{error}</p>
          </div>
        )}

        {!loading && graphData && graphData.nodes.length === 0 && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-400">
            <BookOpen size={40} className="mb-3 opacity-30" />
            <p>尚無 wiki 頁面，請先上傳文件</p>
          </div>
        )}

        {graphData && graphData.nodes.length > 0 && (
          <ForceGraph2D
            ref={fgRef}
            graphData={graphData}
            nodeId="id"
            nodeLabel="title"
            nodeCanvasObject={nodeCanvasObject}
            nodeCanvasObjectMode={() => 'replace'}
            linkColor={(l: any) => {
              if (!hoveredNode) return 'rgba(156,163,175,0.6)'
              const s = typeof l.source === 'string' ? l.source : l.source?.id
              const t = typeof l.target === 'string' ? l.target : l.target?.id
              const connected = s === hoveredNode.id || t === hoveredNode.id
              return connected ? 'rgba(59,130,246,0.75)' : 'rgba(156,163,175,0.1)'
            }}
            linkWidth={(l: any) => {
              if (!hoveredNode) return 1.5
              const s = typeof l.source === 'string' ? l.source : l.source?.id
              const t = typeof l.target === 'string' ? l.target : l.target?.id
              return s === hoveredNode.id || t === hoveredNode.id ? 2.5 : 1
            }}
            linkDirectionalArrowLength={5}
            linkDirectionalArrowRelPos={1}
            linkCurvature={0.1}
            onNodeClick={handleNodeClick}
            onNodeHover={(node) => setHoveredNode(node as GraphNode | null)}
            backgroundColor="#f9fafb"
            width={selectedPage ? window.innerWidth - 380 : window.innerWidth - 56}
            cooldownTicks={120}
            onEngineStop={() => fgRef.current?.zoomToFit(400, 40)}
          />
        )}
      </div>

      {/* 側邊詳情面板 */}
      {selectedPage && (
        <div className="w-96 bg-white border-l border-gray-200 flex flex-col overflow-hidden">
          <div className="p-4 border-b border-gray-200 flex items-start justify-between gap-2">
            <div>
              <span className="text-xs px-2 py-0.5 rounded-full font-medium"
                style={{ backgroundColor: `${PAGE_TYPE_COLOR[selectedPage.page_type]}20`, color: PAGE_TYPE_COLOR[selectedPage.page_type] }}>
                {PAGE_TYPE_LABEL[selectedPage.page_type] || selectedPage.page_type}
              </span>
              <h2 className="font-bold text-gray-800 mt-1 text-lg leading-tight">{selectedPage.title}</h2>
              <p className="text-xs text-gray-400 mt-1">
                更新：{new Date(selectedPage.updated_at).toLocaleString('zh-TW')}
              </p>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0 mt-1">
              <button
                onClick={async () => {
                  if (!confirm(`刪除頁面「${selectedPage.title}」？`)) return
                  try {
                    await deleteWikiPage(selectedPage.id)
                    setSelectedPage(null)
                    load()
                  } catch {}
                }}
                className="text-gray-300 hover:text-red-500 transition-colors"
                title="刪除此頁面"
              >
                <Trash2 size={14} />
              </button>
              <button onClick={() => setSelectedPage(null)} className="text-gray-400 hover:text-gray-600">✕</button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-4 prose prose-sm max-w-none text-sm">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{selectedPage.content}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  )
}
