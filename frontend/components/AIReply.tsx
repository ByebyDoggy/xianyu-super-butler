import React, { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  Bot,
  CheckCircle2,
  Eye,
  EyeOff,
  ExternalLink,
  Layers,
  Loader2,
  MessageSquareText,
  Pencil,
  Play,
  Plus,
  Save,
  ShieldCheck,
  Trash2,
  UserRound,
  X,
} from 'lucide-react';
import { AccountDetail, AIReplySettings, AIReplyOverride, AIReplyPromptPreview } from '../types';
import {
  createAIReplyOverride,
  deleteAIReplyOverride,
  getAccountAISettings,
  getAccountDetails,
  getAIReplyOverrides,
  previewAIReplyPrompt,
  testAIConnection,
  updateAccountAISettings,
  updateAIReplyOverride,
} from '../services/api';
import { confirmAction, notify } from '../services/feedback';
import { EmptyState, PageHeader, PageLoading, SectionHeader } from './ui';

// 自建中转，兼容 OpenAI 接口，每天可领免费额度，省去用户自己找服务商配密钥。
const FREE_TOKEN_BASE_URL = 'https://ai.corleom.com/v1';
const FREE_TOKEN_HOME = 'https://ai.corleom.com';

const defaultSettings: AIReplySettings = {
  ai_enabled: false,
  model_name: 'qwen-plus',
  api_key: '',
  api_key_configured: false,
  base_url: FREE_TOKEN_BASE_URL,
  user_agent: 'codex_cli_rs/0.0.0 (Hermes Agent)',
  max_discount_percent: 10,
  max_discount_amount: 100,
  max_bargain_rounds: 3,
  context_enabled: true,
  context_message_limit: 12,
  context_expire_minutes: 120,
  custom_prompts: '',
};

const AIReply: React.FC = () => {
  const [accounts, setAccounts] = useState<AccountDetail[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState('');
  const [settings, setSettings] = useState<AIReplySettings>(defaultSettings);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);
  const [testMessage, setTestMessage] = useState('你好，这个商品现在还能买吗？');
  const [testReply, setTestReply] = useState('');

  // 提示词预览：不调用模型，只把最终拼出来的结构展示出来
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<AIReplyPromptPreview | null>(null);
  const [previewIntent, setPreviewIntent] = useState('');
  const [previewMessage, setPreviewMessage] = useState('能便宜点吗？');
  const [previewBuyerId, setPreviewBuyerId] = useState('');
  const [previewItemId, setPreviewItemId] = useState('');
  const [showAllMessages, setShowAllMessages] = useState(false);

  // 买家/商品专属规则
  const [overrides, setOverrides] = useState<AIReplyOverride[]>([]);
  const [overrideModalOpen, setOverrideModalOpen] = useState(false);
  const [editingOverrideId, setEditingOverrideId] = useState<number | null>(null);
  const [savingOverride, setSavingOverride] = useState(false);
  const [overrideForm, setOverrideForm] = useState({
    buyer_id: '',
    item_id: '',
    name: '',
    custom_prompts: '',
    knowledge: '',
    priority: 0,
    enabled: true,
  });

  const selectedAccount = useMemo(
    () => accounts.find(account => account.id === selectedAccountId),
    [accounts, selectedAccountId],
  );

  useEffect(() => {
    getAccountDetails()
      .then(data => {
        setAccounts(data);
        setSelectedAccountId(data[0]?.id || '');
      })
      .catch(error => notify(error instanceof Error ? error.message : '账号加载失败', 'error'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selectedAccountId) return;
    setLoading(true);
    setTestReply('');
    setShowApiKey(false);
    setPreview(null);
    getAccountAISettings(selectedAccountId)
      .then(data => setSettings({ ...defaultSettings, ...data, api_key: '' }))
      .catch(error => notify(error instanceof Error ? error.message : 'AI配置加载失败', 'error'))
      .finally(() => setLoading(false));
  }, [selectedAccountId]);

  // 切账号时重新拉专属规则
  useEffect(() => {
    if (!selectedAccountId) {
      setOverrides([]);
      return;
    }
    getAIReplyOverrides(selectedAccountId)
      .then(list => setOverrides(Array.isArray(list) ? list : []))
      .catch(() => setOverrides([]));
  }, [selectedAccountId]);

  const updateSetting = <K extends keyof AIReplySettings>(key: K, value: AIReplySettings[K]) => {
    setSettings(current => ({ ...current, [key]: value }));
  };

  const handleSave = async () => {
    if (!selectedAccountId) {
      notify('请先选择账号', 'warning');
      return;
    }
    if (!settings.model_name.trim() || !settings.base_url.trim()) {
      notify('模型名称和接口地址不能为空', 'warning');
      return;
    }

    setSaving(true);
    try {
      await updateAccountAISettings(selectedAccountId, settings);
      const refreshed = await getAccountAISettings(selectedAccountId);
      setSettings({ ...defaultSettings, ...refreshed, api_key: '' });
      notify('人工智能回复配置已保存', 'success');
    } catch (error) {
      notify(error instanceof Error ? error.message : 'AI配置保存失败', 'error');
    } finally {
      setSaving(false);
    }
  };

  const reloadOverrides = async () => {
    if (!selectedAccountId) return;
    try {
      const list = await getAIReplyOverrides(selectedAccountId);
      setOverrides(Array.isArray(list) ? list : []);
    } catch {
      /* 列表拉不到不影响其它操作 */
    }
  };

  const handlePreview = async () => {
    if (!selectedAccountId) {
      notify('请先选择账号', 'warning');
      return;
    }
    setPreviewing(true);
    try {
      const result = await previewAIReplyPrompt(selectedAccountId, {
        message: previewMessage.trim(),
        intent: previewIntent || undefined,
        buyer_id: previewBuyerId.trim() || undefined,
        item_id: previewItemId.trim() || undefined,
      });
      setPreview(result);
      setShowAllMessages(false);
    } catch (error) {
      notify(error instanceof Error ? error.message : '提示词预览失败', 'error');
    } finally {
      setPreviewing(false);
    }
  };

  const openOverrideModal = (override?: AIReplyOverride) => {
    if (override) {
      setEditingOverrideId(override.id);
      setOverrideForm({
        buyer_id: override.buyer_id || '',
        item_id: override.item_id || '',
        name: override.name || '',
        custom_prompts: override.custom_prompts || '',
        knowledge: override.knowledge || '',
        priority: override.priority || 0,
        enabled: override.enabled !== false,
      });
    } else {
      setEditingOverrideId(null);
      setOverrideForm({
        buyer_id: previewBuyerId.trim(),
        item_id: previewItemId.trim(),
        name: '',
        custom_prompts: '',
        knowledge: '',
        priority: 0,
        enabled: true,
      });
    }
    setOverrideModalOpen(true);
  };

  const handleSaveOverride = async () => {
    if (!selectedAccountId) return;
    if (!overrideForm.buyer_id.trim() && !overrideForm.item_id.trim()) {
      notify('买家 ID 和商品 ID 至少要填一个', 'warning');
      return;
    }
    if (!overrideForm.custom_prompts.trim() && !overrideForm.knowledge.trim()) {
      notify('专属提示词和专属知识至少要填一项', 'warning');
      return;
    }
    setSavingOverride(true);
    try {
      if (editingOverrideId) {
        await updateAIReplyOverride(selectedAccountId, editingOverrideId, overrideForm);
        notify('专属规则已更新', 'success');
      } else {
        await createAIReplyOverride(selectedAccountId, overrideForm);
        notify('专属规则已新增', 'success');
      }
      setOverrideModalOpen(false);
      await reloadOverrides();
    } catch (error) {
      notify(error instanceof Error ? error.message : '保存失败', 'error');
    } finally {
      setSavingOverride(false);
    }
  };

  const handleDeleteOverride = async (override: AIReplyOverride) => {
    if (!selectedAccountId) return;
    if (!(await confirmAction(`确认删除规则「${override.name || override.item_id || override.buyer_id}」？`))) {
      return;
    }
    try {
      await deleteAIReplyOverride(selectedAccountId, override.id);
      notify('已删除', 'success');
      await reloadOverrides();
    } catch (error) {
      notify(error instanceof Error ? error.message : '删除失败', 'error');
    }
  };

  const handleTest = async () => {
    if (!selectedAccountId || !testMessage.trim()) {
      notify('请选择账号并输入测试消息', 'warning');
      return;
    }
    setTesting(true);
    setTestReply('');
    try {
      const result = await testAIConnection(selectedAccountId, {
        message: testMessage.trim(),
        item_title: '测试商品',
        item_price: 100,
        item_desc: '仅用于测试人工智能回复，不会发送到闲鱼。',
      });
      setTestReply(result.reply || result.message || '测试完成');
      notify('AI回复测试完成', 'success');
    } catch (error) {
      notify(error instanceof Error ? error.message : 'AI回复测试失败', 'error');
    } finally {
      setTesting(false);
    }
  };

  if (loading && accounts.length === 0) {
    return <PageLoading label="正在加载 AI 回复配置" />;
  }

  return (
    <div className="page-stack animate-fade-in">
      <PageHeader
        title="AI 回复"
        description="按账号配置模型连接、上下文记忆、议价边界和业务回复规则。"
        icon={Bot}
        actions={(
          <div className="flex min-w-0 flex-wrap items-end gap-2">
            <label className="min-w-0 sm:w-72">
              <span className="field-label">当前账号</span>
              <select
                value={selectedAccountId}
                onChange={event => setSelectedAccountId(event.target.value)}
                className="ios-input w-full rounded-md px-3 py-2 text-sm font-semibold"
              >
                {accounts.map(account => (
                  <option key={account.id} value={account.id}>
                    {account.nickname || account.remark || `账号 ${account.id.slice(0, 8)}`}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || !selectedAccountId}
              className="ios-btn-primary flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm"
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              {saving ? '保存中' : '保存配置'}
            </button>
          </div>
        )}
      />

      {!selectedAccountId ? (
        <EmptyState
          icon={Bot}
          title="暂无可配置账号"
          description="请先在账号管理中添加并登录闲鱼账号。"
        />
      ) : (
        <>
          <section className="section-panel grid gap-4 p-4 lg:grid-cols-[1fr_auto] lg:items-center">
            <div>
              <div className="flex items-center gap-2 font-bold text-gray-900">
                <MessageSquareText className="h-5 w-5" />
                {selectedAccount?.nickname || selectedAccount?.remark || selectedAccountId}
              </div>
              <p className="mt-1 text-sm text-gray-500">
                回复优先级：关键词回复 → 人工智能回复 → 默认回复。AI失败时不会中断消息处理。
              </p>
            </div>
            <label className="flex cursor-pointer items-center gap-3">
              <span className="text-sm font-bold text-gray-700">
                {settings.ai_enabled ? '已启用' : '已停用'}
              </span>
              <input
                type="checkbox"
                checked={settings.ai_enabled}
                onChange={event => updateSetting('ai_enabled', event.target.checked)}
                className="h-5 w-5 accent-yellow-400"
              />
            </label>
          </section>

          <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(340px,0.65fr)]">
            <div className="space-y-6">
              <section className="section-panel">
                <SectionHeader
                  title="模型连接"
                  description="支持 OpenAI 兼容接口；密钥留空时保留服务器中已有配置。"
                  icon={Bot}
                />
                <div className="grid gap-4 p-5 md:grid-cols-2">
                  <div className="md:col-span-2 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-brand-200 bg-brand-50 px-4 py-3">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-gray-800">
                        没有 API Key？每天可免费领取额度
                      </p>
                      <p className="mt-0.5 text-xs text-gray-600">
                        兼容 OpenAI 接口，注册后把密钥填到下方即可直接用。
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <a
                        href={FREE_TOKEN_HOME}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="inline-flex items-center gap-1 rounded-md bg-brand-500 px-3 py-1.5 text-xs font-semibold text-brand-ink hover:bg-brand-600"
                      >
                        免费领取 token
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    </div>
                  </div>
                  <label>
                    <span className="mb-1.5 block text-sm font-semibold text-gray-700">接口地址</span>
                    <input
                      value={settings.base_url}
                      onChange={event => updateSetting('base_url', event.target.value)}
                      className="ios-input w-full rounded-md px-3 py-2.5 text-sm"
                      placeholder={FREE_TOKEN_BASE_URL}
                    />
                  </label>
                  <label>
                    <span className="mb-1.5 block text-sm font-semibold text-gray-700">模型名称</span>
                    <input
                      value={settings.model_name}
                      onChange={event => updateSetting('model_name', event.target.value)}
                      className="ios-input w-full rounded-md px-3 py-2.5 text-sm"
                      placeholder="qwen-plus"
                    />
                  </label>
                  <label className="md:col-span-2">
                    <span className="mb-1.5 flex items-center justify-between gap-3 text-sm font-semibold text-gray-700">
                      API Key
                      {settings.api_key_configured && !settings.api_key && (
                        <span className="flex items-center gap-1 text-xs font-medium text-emerald-700">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          已配置，留空保持不变
                        </span>
                      )}
                    </span>
                    <div className="relative">
                      <input
                        type={showApiKey ? 'text' : 'password'}
                        value={settings.api_key}
                        onChange={event => updateSetting('api_key', event.target.value)}
                        className="ios-input w-full rounded-md px-3 py-2.5 pr-10 text-sm"
                        placeholder={settings.api_key_configured ? '输入新密钥以替换' : '请输入 API Key'}
                        autoComplete="new-password"
                      />
                      <button
                        type="button"
                        onClick={() => setShowApiKey(value => !value)}
                        className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-500 hover:bg-gray-100"
                        title={showApiKey ? '隐藏密钥' : '显示密钥'}
                      >
                        {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                    </div>
                  </label>
                  <label className="md:col-span-2">
                    <span className="mb-1.5 block text-sm font-semibold text-gray-700">User-Agent</span>
                    <input
                      value={settings.user_agent ?? ''}
                      onChange={event => updateSetting('user_agent', event.target.value)}
                      className="ios-input w-full rounded-md px-3 py-2.5 text-sm"
                      placeholder="codex_cli_rs/0.0.0 (Hermes Agent)"
                    />
                    <span className="mt-1 block text-xs text-gray-500">部分 API 中转站（如 AgentRouter）通过 User-Agent 白名单检测客户端，留空使用默认值。</span>
                  </label>
                </div>
              </section>

              <section className="section-panel">
                <SectionHeader
                  title="回复策略"
                  description="约束议价空间，并补充账号专属的语气、承诺和售后规则。"
                  icon={MessageSquareText}
                />
                <div className="grid gap-4 p-5 sm:grid-cols-3">
                  <label>
                    <span className="mb-1.5 block text-sm font-semibold text-gray-700">最大折扣比例</span>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={settings.max_discount_percent}
                      onChange={event => updateSetting('max_discount_percent', Number(event.target.value))}
                      className="ios-input w-full rounded-md px-3 py-2.5 text-sm"
                    />
                  </label>
                  <label>
                    <span className="mb-1.5 block text-sm font-semibold text-gray-700">最大折扣金额</span>
                    <input
                      type="number"
                      min={0}
                      value={settings.max_discount_amount ?? 0}
                      onChange={event => updateSetting('max_discount_amount', Number(event.target.value))}
                      className="ios-input w-full rounded-md px-3 py-2.5 text-sm"
                    />
                  </label>
                  <label>
                    <span className="mb-1.5 block text-sm font-semibold text-gray-700">最大议价轮次</span>
                    <input
                      type="number"
                      min={1}
                      max={10}
                      value={settings.max_bargain_rounds}
                      onChange={event => updateSetting('max_bargain_rounds', Number(event.target.value))}
                      className="ios-input w-full rounded-md px-3 py-2.5 text-sm"
                    />
                  </label>
                  <label className="sm:col-span-3">
                    <span className="mb-1.5 block text-sm font-semibold text-gray-700">回复风格与业务规则</span>
                    <textarea
                      value={settings.custom_prompts}
                      onChange={event => updateSetting('custom_prompts', event.target.value)}
                      className="ios-input min-h-36 w-full resize-y rounded-md px-3 py-2.5 text-sm leading-6"
                      placeholder="例如：语气简洁，不承诺未确认的库存；涉及售后时引导买家说明订单号。"
                    />
                  </label>
                </div>
              </section>

              <section className="section-panel">
                <SectionHeader
                  title="上下文对话"
                  description="控制单个买家会话中可用于连续回复的近期消息范围。"
                  icon={ShieldCheck}
                />
                <div className="grid gap-4 p-5 sm:grid-cols-2">
                  <label className="flex items-center justify-between gap-4 sm:col-span-2">
                    <span>
                      <span className="block text-sm font-semibold text-gray-700">记住近期对话</span>
                      <span className="mt-1 block text-xs leading-5 text-gray-500">
                        上下文按账号、会话和商品隔离，切换商品不会混入旧商品内容。
                      </span>
                    </span>
                    <input
                      type="checkbox"
                      checked={settings.context_enabled}
                      onChange={event => updateSetting('context_enabled', event.target.checked)}
                      className="h-5 w-5 shrink-0 accent-yellow-400"
                    />
                  </label>
                  <label>
                    <span className="mb-1.5 block text-sm font-semibold text-gray-700">记忆消息数</span>
                    <input
                      type="number"
                      min={2}
                      max={30}
                      disabled={!settings.context_enabled}
                      value={settings.context_message_limit}
                      onChange={event => updateSetting('context_message_limit', Number(event.target.value))}
                      className="ios-input w-full rounded-md px-3 py-2.5 text-sm disabled:bg-gray-100"
                    />
                  </label>
                  <label>
                    <span className="mb-1.5 block text-sm font-semibold text-gray-700">上下文有效期（分钟）</span>
                    <input
                      type="number"
                      min={5}
                      max={1440}
                      disabled={!settings.context_enabled}
                      value={settings.context_expire_minutes}
                      onChange={event => updateSetting('context_expire_minutes', Number(event.target.value))}
                      className="ios-input w-full rounded-md px-3 py-2.5 text-sm disabled:bg-gray-100"
                    />
                  </label>
                  <p className="text-xs leading-5 text-gray-500 sm:col-span-2">
                    付款、发货、退款、收货等系统事件不会交给大模型，将继续由订单状态和自动发货规则处理。
                  </p>
                </div>
              </section>

              <section className="section-panel">
                <SectionHeader
                  title="系统提示词预览"
                  description="看一眼最终发给模型的结构：角色设定 + 商品事实 + 议价设置 + 安全边界。预览不调用模型，不消耗额度。"
                  icon={Layers}
                />
                <div className="grid gap-4 p-5">
                  <div className="grid gap-3 sm:grid-cols-4">
                    <label>
                      <span className="mb-1.5 block text-sm font-semibold text-gray-700">意图</span>
                      <select
                        value={previewIntent}
                        onChange={event => setPreviewIntent(event.target.value)}
                        className="ios-input w-full rounded-md px-3 py-2.5 text-sm"
                      >
                        <option value="">自动判断</option>
                        {(preview?.intent_options || ['price', 'tech', 'default']).map(option => (
                          <option key={option} value={option}>
                            {option === 'price' ? '议价（price）' : option === 'tech' ? '技术（tech）' : `通用（${option}）`}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="sm:col-span-3">
                      <span className="mb-1.5 block text-sm font-semibold text-gray-700">模拟买家消息</span>
                      <input
                        value={previewMessage}
                        onChange={event => setPreviewMessage(event.target.value)}
                        className="ios-input w-full rounded-md px-3 py-2.5 text-sm"
                      />
                    </label>
                    <label className="sm:col-span-2">
                      <span className="mb-1.5 block text-sm font-semibold text-gray-700">买家 ID（可选）</span>
                      <input
                        value={previewBuyerId}
                        onChange={event => setPreviewBuyerId(event.target.value)}
                        placeholder="用于验证买家级规则是否命中"
                        className="ios-input w-full rounded-md px-3 py-2.5 text-sm"
                      />
                    </label>
                    <label>
                      <span className="mb-1.5 block text-sm font-semibold text-gray-700">商品 ID（可选）</span>
                      <input
                        value={previewItemId}
                        onChange={event => setPreviewItemId(event.target.value)}
                        placeholder="会自动带出商品事实"
                        className="ios-input w-full rounded-md px-3 py-2.5 text-sm"
                      />
                    </label>
                    <div className="flex items-end">
                      <button
                        type="button"
                        onClick={handlePreview}
                        disabled={previewing}
                        className="ios-btn-secondary flex w-full items-center justify-center gap-2 rounded-md px-4 py-2.5 text-sm"
                      >
                        {previewing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Layers className="h-4 w-4" />}
                        生成预览
                      </button>
                    </div>
                  </div>

                  {preview && (
                    <div className="space-y-3">
                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        <span className={`status-badge ${preview.ai_enabled ? 'status-badge-success' : 'status-badge-warning'}`}>
                          {preview.ai_enabled ? 'AI 已启用' : 'AI 未启用'}
                        </span>
                        <span className="status-badge status-badge-info">模型 {preview.model_name || '-'}</span>
                        <span className="status-badge status-badge-info">意图 {preview.intent}</span>
                        <span className="status-badge status-badge-info">上下文 {preview.context_message_count} 条</span>
                        {preview.override ? (
                          <span className="status-badge bg-emerald-100 text-emerald-800">
                            命中专属规则：{preview.override.name || '（未命名）'}
                            （买家 {preview.override.buyer_id || '不限'} / 商品 {preview.override.item_id || '不限'}；
                            {preview.override.used_custom_prompts ? '提示词已覆盖' : '提示词用账号级'}；
                            知识 {preview.override.knowledge_chars} 字）
                          </span>
                        ) : (
                          <span className="status-badge bg-gray-100 text-gray-600">未命中专属规则，使用账号级配置</span>
                        )}
                      </div>

                      <div>
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-sm font-semibold text-gray-700">
                            system 消息（角色设定 + 商品事实 + 议价设置 + 安全边界）
                          </span>
                          <button
                            type="button"
                            onClick={() => {
                              void navigator.clipboard?.writeText(preview.system_message);
                              notify('已复制 system 消息', 'success');
                            }}
                            className="shrink-0 rounded-md px-2 py-1 text-xs font-bold text-blue-600 hover:bg-blue-50"
                          >
                            复制
                          </button>
                        </div>
                        <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md bg-gray-900 px-3 py-2.5 font-mono text-[11px] leading-5 text-gray-100">
{preview.system_message}
                        </pre>
                      </div>

                      <div>
                        <button
                          type="button"
                          onClick={() => setShowAllMessages(value => !value)}
                          className="text-xs font-bold text-blue-600 hover:underline"
                        >
                          {showAllMessages ? '收起' : '查看'}完整 messages（{preview.messages.length} 条）
                        </button>
                        {showAllMessages && (
                          <ol className="mt-2 space-y-2">
                            {preview.messages.map((msg, index) => (
                              <li key={`${msg.role}-${index}`}>
                                <span className="status-badge status-badge-info">{msg.role}</span>
                                <pre className="mt-1 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-md bg-gray-50 px-3 py-2 font-mono text-[11px] leading-5 text-gray-700">
{msg.content}
                                </pre>
                              </li>
                            ))}
                          </ol>
                        )}
                      </div>

                      <details className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2">
                        <summary className="cursor-pointer text-xs font-bold text-gray-700">
                          内置默认提示词与账号级配置（只读，用于对照）
                        </summary>
                        <div className="mt-2 space-y-2 text-xs">
                          <div>
                            <span className="font-semibold text-gray-700">账号级「回复风格与业务规则」：</span>
                            <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] text-gray-600">
{preview.account_custom_prompts || '（未填写，使用内置默认提示词）'}
                            </pre>
                          </div>
                          {Object.entries(preview.default_prompts || {})
                            .filter(([key]) => preview.intent_options.includes(key))
                            .map(([key, value]) => (
                              <div key={key}>
                                <span className="font-semibold text-gray-700">内置默认（{key}）：</span>
                                <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] text-gray-600">{value}</pre>
                              </div>
                            ))}
                        </div>
                      </details>

                      <p className="text-xs leading-5 text-gray-500">
                        拼装逻辑与真实回复完全共用，所以这里看到的就是实际会发给模型的内容
                        （预览不包含尚未发生的模型回复）。
                      </p>
                    </div>
                  )}
                </div>
              </section>

              <section className="section-panel">
                <SectionHeader
                  title="买家 / 商品专属提示词"
                  description="给「某个买家的某个商品」单独设定提示词与知识，命中时优先于账号级配置。"
                  icon={UserRound}
                  actions={(
                    <button
                      type="button"
                      onClick={() => openOverrideModal()}
                      className="ios-btn-primary flex items-center gap-2 rounded-md px-3 py-2 text-xs"
                    >
                      <Plus className="h-3.5 w-3.5" />
                      新增规则
                    </button>
                  )}
                />
                <div className="p-5">
                  <p className="mb-3 text-xs leading-5 text-gray-500">
                    匹配优先级：买家+商品（最具体） &gt; 只限买家 / 只限商品 &gt; 都不限（等同账号级）；
                    同样命中时比较「优先级」，数值大的生效。留空的维度表示「不限」。
                  </p>

                  {overrides.length === 0 ? (
                    <EmptyState
                      compact
                      title="暂无专属规则"
                      description="例如：给老客户单独放宽松的议价口径，或给某个商品补充专用知识。"
                      icon={UserRound}
                    />
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-gray-200 text-left text-xs text-gray-500">
                            <th className="py-2 pr-3">名称</th>
                            <th className="py-2 pr-3">买家 ID</th>
                            <th className="py-2 pr-3">商品 ID</th>
                            <th className="py-2 pr-3">专属提示词</th>
                            <th className="py-2 pr-3">专属知识</th>
                            <th className="py-2 pr-3">优先级</th>
                            <th className="py-2 pr-3">状态</th>
                            <th className="py-2">操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          {overrides.map(override => (
                            <tr key={override.id} className="border-b border-gray-100">
                              <td className="py-2 pr-3 font-semibold text-gray-800">{override.name || '-'}</td>
                              <td className="py-2 pr-3 font-mono text-xs text-gray-600">{override.buyer_id || '不限'}</td>
                              <td className="py-2 pr-3 font-mono text-xs text-gray-600">{override.item_id || '不限'}</td>
                              <td className="py-2 pr-3 text-xs text-gray-600">
                                {override.custom_prompts ? `${override.custom_prompts.length} 字` : '（用账号级）'}
                              </td>
                              <td className="py-2 pr-3 text-xs text-gray-600">
                                {override.knowledge ? `${override.knowledge.length} 字` : '-'}
                              </td>
                              <td className="py-2 pr-3 text-xs text-gray-600">{override.priority || 0}</td>
                              <td className="py-2 pr-3">
                                <span className={`status-badge ${override.enabled ? 'status-badge-success' : 'bg-gray-100 text-gray-500'}`}>
                                  {override.enabled ? '启用' : '停用'}
                                </span>
                              </td>
                              <td className="py-2">
                                <div className="flex items-center gap-1">
                                  <button
                                    type="button"
                                    onClick={() => openOverrideModal(override)}
                                    title="编辑"
                                    className="rounded-md p-1.5 text-gray-500 hover:bg-gray-100"
                                  >
                                    <Pencil className="h-3.5 w-3.5" />
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => handleDeleteOverride(override)}
                                    title="删除"
                                    className="rounded-md p-1.5 text-red-500 hover:bg-red-50"
                                  >
                                    <Trash2 className="h-3.5 w-3.5" />
                                  </button>
                                </div>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </section>
            </div>

            <aside className="space-y-5">
              <section className="section-panel">
                <SectionHeader
                  title="回复测试"
                  description="只生成文本，不会发送到闲鱼会话。"
                  icon={Play}
                />
                <div className="p-5">
                  <textarea
                    value={testMessage}
                    onChange={event => setTestMessage(event.target.value)}
                    className="ios-input min-h-24 w-full resize-y rounded-md px-3 py-2.5 text-sm"
                  />
                  <button
                    type="button"
                    onClick={handleTest}
                    disabled={testing || !settings.ai_enabled}
                    className="ios-btn-primary mt-3 flex w-full items-center justify-center gap-2 rounded-md px-4 py-2.5 text-sm"
                  >
                    {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                    {testing ? '生成中' : '测试回复'}
                  </button>
                  {testReply && (
                    <div className="mt-4 border-l-4 border-yellow-400 bg-yellow-50 px-4 py-3 text-sm leading-6 text-gray-800">
                      {testReply}
                    </div>
                  )}
                </div>
              </section>

              <section className="section-panel p-5">
                <div className="flex items-start gap-3">
                  <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-700" />
                  <div>
                    <h2 className="text-sm font-bold text-gray-900">运行保护</h2>
                    <p className="mt-1 text-xs leading-5 text-gray-500">
                      密钥不会回传到浏览器；接口超时、空回复或格式异常时，系统会自动继续使用默认回复。
                    </p>
                  </div>
                </div>
              </section>
            </aside>
          </div>

        </>
      )}

      {overrideModalOpen && createPortal(
        <div className="modal-overlay">
          <div className="modal-container" style={{ maxWidth: '40rem' }}>
            <div className="modal-header flex items-start justify-between gap-4">
              <div>
                <h3 className="text-lg font-bold text-gray-900">
                  {editingOverrideId ? '编辑专属规则' : '新增专属规则'}
                </h3>
                <p className="mt-1 text-xs text-gray-500">
                  买家 ID 与商品 ID 至少要填一个；留空的那一维表示「不限」。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setOverrideModalOpen(false)}
                className="shrink-0 rounded-md p-2 hover:bg-gray-100"
                aria-label="关闭"
              >
                <X className="h-5 w-5 text-gray-500" />
              </button>
            </div>

            <div className="modal-body space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <label>
                  <span className="mb-1.5 block text-sm font-semibold text-gray-700">买家 ID</span>
                  <input
                    value={overrideForm.buyer_id}
                    onChange={event => setOverrideForm({ ...overrideForm, buyer_id: event.target.value })}
                    placeholder="留空 = 不限买家"
                    className="ios-input w-full rounded-md px-3 py-2.5 text-sm"
                  />
                </label>
                <label>
                  <span className="mb-1.5 block text-sm font-semibold text-gray-700">商品 ID</span>
                  <input
                    value={overrideForm.item_id}
                    onChange={event => setOverrideForm({ ...overrideForm, item_id: event.target.value })}
                    placeholder="留空 = 不限商品"
                    className="ios-input w-full rounded-md px-3 py-2.5 text-sm"
                  />
                </label>
                <label>
                  <span className="mb-1.5 block text-sm font-semibold text-gray-700">规则名称（便于识别）</span>
                  <input
                    value={overrideForm.name}
                    onChange={event => setOverrideForm({ ...overrideForm, name: event.target.value })}
                    placeholder="例如：老客户-某商品"
                    className="ios-input w-full rounded-md px-3 py-2.5 text-sm"
                  />
                </label>
                <label>
                  <span className="mb-1.5 block text-sm font-semibold text-gray-700">优先级</span>
                  <input
                    type="number"
                    value={overrideForm.priority}
                    onChange={event => setOverrideForm({ ...overrideForm, priority: Number(event.target.value) })}
                    className="ios-input w-full rounded-md px-3 py-2.5 text-sm"
                  />
                </label>
              </div>

              <label className="block">
                <span className="mb-1.5 block text-sm font-semibold text-gray-700">
                  专属提示词（留空则继续用账号级的「回复风格与业务规则」）
                </span>
                <textarea
                  value={overrideForm.custom_prompts}
                  onChange={event => setOverrideForm({ ...overrideForm, custom_prompts: event.target.value })}
                  className="ios-input min-h-28 w-full resize-y rounded-md px-3 py-2.5 text-sm leading-6"
                  placeholder="例如：这位买家是老客户，语气亲切，可在授权范围内主动给出小优惠。"
                />
              </label>

              <label className="block">
                <span className="mb-1.5 block text-sm font-semibold text-gray-700">
                  专属知识（会以「额外知识」小节追加到系统提示词，不覆盖角色设定）
                </span>
                <textarea
                  value={overrideForm.knowledge}
                  onChange={event => setOverrideForm({ ...overrideForm, knowledge: event.target.value })}
                  className="ios-input min-h-28 w-full resize-y rounded-md px-3 py-2.5 text-sm leading-6"
                  placeholder="例如：该买家已复购 3 次，历史订单均可正常发货；该商品为虚拟卡密，不支持无理由退款。"
                />
              </label>

              <label className="flex items-center justify-between gap-4">
                <span className="text-sm font-semibold text-gray-700">启用这条规则</span>
                <input
                  type="checkbox"
                  checked={overrideForm.enabled}
                  onChange={event => setOverrideForm({ ...overrideForm, enabled: event.target.checked })}
                  className="h-5 w-5 shrink-0 accent-yellow-400"
                />
              </label>
            </div>

            <div className="modal-footer flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setOverrideModalOpen(false)}
                className="ios-btn-secondary rounded-md px-4 py-2.5 text-sm"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleSaveOverride}
                disabled={savingOverride}
                className="ios-btn-primary flex items-center gap-2 rounded-md px-4 py-2.5 text-sm"
              >
                {savingOverride ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                保存
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
};

export default AIReply;
