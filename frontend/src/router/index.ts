import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
const Experiment = () => import('@/views/experiment/index.vue')
const SampleGroup = () => import('@/views/sample_group/index.vue')
const SampleGroupDetail = () => import('@/views/sample_group/detail.vue')
const MetricDef = () => import('@/views/metric_def/index.vue')
const AbResult = () => import('@/views/ab_result/index.vue')
const SegmentCompare = () => import('@/views/segment_compare/index.vue')
const DrillDown = () => import('@/views/drill_down/index.vue')
const DataFilter = () => import('@/views/data_filter/index.vue')
const AttrAnalysis = () => import('@/views/attr_analysis/index.vue')
const AnomalyDetect = () => import('@/views/anomaly_detect/index.vue')
const ForecastModel = () => import('@/views/forecast_model/index.vue')
const DataExport = () => import('@/views/data_export/index.vue')
const CohortTrack = () => import('@/views/cohort_track/index.vue')
const ChartConfig = () => import('@/views/chart_config/index.vue')
const InsightReport = () => import('@/views/insight_report/index.vue')
const DataFreshness = () => import('@/views/data_freshness/index.vue')
const MetricAlias = () => import('@/views/metric_alias/index.vue')
const ExprFactor = () => import('@/views/expr_factor/index.vue')
const EvaluateBoard = () => import('@/views/evaluate_board/index.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/experiment', name: 'experiment', component: Experiment },
    { path: '/sample_group', name: 'sample_group', component: SampleGroup },
    { path: '/sample_group/:id', name: 'sample_group_detail', component: SampleGroupDetail },
    { path: '/metric_def', name: 'metric_def', component: MetricDef },
    { path: '/ab_result', name: 'ab_result', component: AbResult },
    { path: '/segment_compare', name: 'segment_compare', component: SegmentCompare },
    { path: '/drill_down', name: 'drill_down', component: DrillDown },
    { path: '/data_filter', name: 'data_filter', component: DataFilter },
    { path: '/attr_analysis', name: 'attr_analysis', component: AttrAnalysis },
    { path: '/anomaly_detect', name: 'anomaly_detect', component: AnomalyDetect },
    { path: '/forecast_model', name: 'forecast_model', component: ForecastModel },
    { path: '/data_export', name: 'data_export', component: DataExport },
    { path: '/cohort_track', name: 'cohort_track', component: CohortTrack },
    { path: '/chart_config', name: 'chart_config', component: ChartConfig },
    { path: '/insight_report', name: 'insight_report', component: InsightReport },
    { path: '/data_freshness', name: 'data_freshness', component: DataFreshness },
    { path: '/metric_alias', name: 'metric_alias', component: MetricAlias },
    { path: '/expr_factor', name: 'expr_factor', component: ExprFactor },
    { path: '/evaluate_board', name: 'evaluate_board', component: EvaluateBoard },
  ],
})

export default router
