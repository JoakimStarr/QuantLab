import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/auth/Login.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/',
    component: () => import('@/components/layout/AppLayout.vue'),
    children: [
      {
        path: '',
        name: 'Dashboard',
        component: () => import('@/views/quant/Dashboard.vue'),
        meta: {
          title: '研究首页',
          icon: 'DataAnalysis',
          transition: 'fade-in-up',
          keepAlive: true,
        },
      },
      {
        path: 'quant/factors',
        name: 'FactorLibrary',
        component: () => import('@/views/quant/FactorLibrary.vue'),
        meta: {
          title: '因子库',
          icon: 'Coin',
          transition: 'fade-in-up',
          keepAlive: true,
        },
      },
      {
        path: 'quant/strategy',
        name: 'QuantStrategy',
        component: () => import('@/views/quant/Strategy.vue'),
        meta: {
          title: '策略回测',
          icon: 'TrendCharts',
          transition: 'fade-in-up',
          keepAlive: true,
        },
      },
      {
        path: 'quant/strategy-library',
        name: 'QuantStrategyLibrary',
        component: () => import('@/views/quant/StrategyLibrary.vue'),
        meta: {
          title: '策略库',
          icon: 'Collection',
          transition: 'fade-in-up',
          keepAlive: true,
        },
      },
      {
        path: 'quant/mining',
        name: 'Mining',
        component: () => import('@/views/quant/Mining.vue'),
        meta: {
          title: 'AI因子挖掘',
          icon: 'MagicStick',
          transition: 'fade-in-up',
          keepAlive: false, // Mining page should refresh each time
        },
      },
      {
        path: 'quant/data',
        name: 'QuantData',
        component: () => import('@/views/quant/DataStatus.vue'),
        meta: {
          title: '数据管理',
          icon: 'SetUp',
          transition: 'fade-in-up',
          keepAlive: true,
        },
      },
      {
        path: 'quant/macro',
        name: 'Macro',
        component: () => import('@/views/quant/Macro.vue'),
        meta: {
          title: '宏观指标',
          icon: 'Odometer',
          transition: 'fade-in-up',
          keepAlive: true,
        },
      },
      {
        path: 'quant/factor-compare',
        name: 'FactorCompare',
        component: () => import('@/views/quant/FactorCompare.vue'),
        meta: {
          title: '因子对比',
          transition: 'fade-in-up',
          keepAlive: false,
        },
      },
      {
        path: 'quant/backtest-compare',
        name: 'BacktestCompare',
        component: () => import('@/views/quant/BacktestCompare.vue'),
        meta: {
          title: '回测对比',
          transition: 'fade-in-up',
          keepAlive: false,
        },
      },
      {
        path: 'quant/factor-deep-analysis',
        name: 'FactorDeepAnalysis',
        component: () => import('@/views/quant/FactorDeepAnalysis.vue'),
        meta: {
          title: '因子深度分析',
          transition: 'fade-in-up',
          keepAlive: false,
        },
      },
      {
        path: 'docs/:slug?',
        name: 'Docs',
        component: () => import('@/views/Docs.vue'),
        meta: {
          title: '技术文档',
          icon: 'Reading',
          transition: 'fade-in-up',
          keepAlive: false,
        },
      },
      {
        path: 'system/logs',
        name: 'SystemLogs',
        component: () => import('@/views/quant/Logs.vue'),
        meta: {
          title: '日志管理',
          icon: 'Document',
          transition: 'fade-in-up',
          keepAlive: false,
        },
      },
      {
        path: ':pathMatch(.*)*',
        name: 'NotFound',
        component: () => import('@/views/quant/NotFound.vue'),
        meta: { title: '页面不存在' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(to, from, savedPosition) {
    return savedPosition || { top: 0 }
  },
})

// 鉴权守卫：后端开启鉴权且未登录时，跳转登录页
router.beforeEach(async (to, from, next) => {
  const authStore = useAuthStore()

  // 挂载不再阻塞，鉴权状态在首次导航时同步补齐（fetchStatus 单次探测，失败兜底不锁死）
  if (!authStore.statusLoaded) {
    await authStore.fetchStatus()
  }

  if (to.meta.public) {
    // 鉴权未开启时访问登录页 -> 回首页
    if (to.name === 'Login' && !authStore.authEnabled && authStore.statusLoaded) {
      return next({ path: '/' })
    }
    return next()
  }
  if (authStore.needAuth) {
    return next({ name: 'Login', query: { redirect: to.fullPath } })
  }
  next()
})

export default router
