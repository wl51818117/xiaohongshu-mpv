import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'

/**
 * 注意：这里刻意不启用 StrictMode。
 *
 * X6（@antv/x6 3.x）是命令式图形库，Graph 实例由useEffect 创建与dispose。
 * React 18/19 的 StrictMode 在开发模式下会故意二次挂载组件以暴露副作用问题，
 * 这会导致 X6 重复初始化/ 残留画布节点（典型表现：页面白屏或节点叠加）。
 *
 * 若将来要恢复 StrictMode，需在 FlowCanvas 的 useEffect 里做严格的
 * 幂等保护（用 ref 记录已初始化状态）。
 */
createRoot(document.getElementById('root')!).render(<App />)
