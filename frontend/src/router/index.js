import { createRouter, createWebHistory } from 'vue-router'

const routes = [
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
          keepAlive: true
        }
      },
      {
        path: 'quant/factors',
        name: 'FactorLibrary',
        component: () => import('@/views/quant/FactorLibrary.vue'),
        meta: {
          title: '因子库',
          icon: 'Coin',
          transition: 'fade-in-up',
          keepAlive: true
        }
      },
      {
        path: 'quant/strategy',
        name: 'QuantStrategy',
        component: () => import('@/views/quant/Strategy.vue'),
        meta: {
          title: '策略回测',
          icon: 'TrendCharts',
          transition: 'fade-in-up',
          keepAlive: true
        }
      },
      {
        path: 'quant/mining',
        name: 'Mining',
        component: () => import('@/views/quant/Mining.vue'),
        meta: {
          title: 'AI因子挖掘',
          icon: 'MagicStick',
          transition: 'fade-in-up',
          keepAlive: false // Mining page should refresh each time
        }
      },
      {
        path: 'quant/data',
        name: 'QuantData',
        component: () => import('@/views/quant/DataStatus.vue'),
        meta: {
          title: '数据管理',
          icon: 'SetUp',
          transition: 'fade-in-up',
          keepAlive: true
        }
      },
      {
        path: 'system/logs',
        name: 'SystemLogs',
        component: () => import('@/views/quant/Logs.vue'),
        meta: {
          title: '日志管理',
          icon: 'Document',
          transition: 'fade-in-up',
          keepAlive: false
        }
      },
      {
        path: ':pathMatch(.*)*',
        name: 'NotFound',
        component: () => import('@/views/quant/Dashboard.vue'),
        meta: { title: '页面不存在' }
      },
    ]
  }
]

export default createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(to, from, savedPosition) {
    return savedPosition || { top: 0 }
  }
})
