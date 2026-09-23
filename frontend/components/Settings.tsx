import React, { useEffect, useState } from 'react';
import { getSystemSettings, updateSystemSettings, checkSystemUpdate, applySystemUpdate, UpdateCheckResult } from '../services/api';
import { SystemSettings } from '../types';
import {
  Bot, Save, Lock, Sparkles, Mail, Settings as SettingsIcon,
  Eye, EyeOff, RefreshCw, Database, ToggleLeft, ToggleRight,
  Download, GitBranch, CheckCircle2, AlertTriangle
} from 'lucide-react';

const Settings: React.FC = () => {
  const [settings, setSettings] = useState<SystemSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  // Password visibility states
  const [showApiKey, setShowApiKey] = useState(false);
  const [showSmtpPassword, setShowSmtpPassword] = useState(false);
  const [showGithubToken, setShowGithubToken] = useState(false);

  // Online update states
  const [updateInfo, setUpdateInfo] = useState<UpdateCheckResult | null>(null);
  const [checkingUpdate, setCheckingUpdate] = useState(false);
  const [applyingUpdate, setApplyingUpdate] = useState(false);
  const [updateError, setUpdateError] = useState('');
  const [restartAfterUpdate, setRestartAfterUpdate] = useState(true);

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = () => {
    setLoading(true);
    getSystemSettings().then(setSettings).finally(() => setLoading(false));
  };

  const handleSave = async () => {
      if(!settings) return;
      setSaving(true);
      try {
        await updateSystemSettings(settings);
        alert('系统配置已保存');
      } catch (e) {
        alert('保存失败：' + (e as Error).message);
      } finally {
        setSaving(false);
      }
  };

  const handleCheckUpdate = async () => {
    setCheckingUpdate(true);
    setUpdateError('');
    try {
      setUpdateInfo(await checkSystemUpdate());
    } catch (e) {
      setUpdateError((e as Error).message);
      setUpdateInfo(null);
    } finally {
      setCheckingUpdate(false);
    }
  };

  const handleApplyUpdate = async () => {
    if (!window.confirm('确定要立即在线更新吗？更新完成后服务可能需要重启。')) return;
    setApplyingUpdate(true);
    setUpdateError('');
    try {
      const res = await applySystemUpdate({ restart: restartAfterUpdate, force: false });
      alert('更新完成：' + (res.detail || '') + (res.restart_scheduled ? '，服务正在重启...' : '，请手动重启服务'));
    } catch (e) {
      setUpdateError((e as Error).message);
    } finally {
      setApplyingUpdate(false);
    }
  };

  if (!settings) return <div className="p-8 text-center text-gray-400">加载配置中...</div>;

  return (
    <div className="max-w-6xl mx-auto space-y-8 animate-fade-in pb-24">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 bg-gray-100 rounded-2xl flex items-center justify-center">
              <SettingsIcon className="w-6 h-6 text-gray-600" />
          </div>
          <div>
              <h2 className="text-3xl font-extrabold text-gray-900">系统设置</h2>
              <p className="text-gray-500 mt-1 text-sm font-medium">配置全局自动化规则与系统参数</p>
          </div>
        </div>
        <button
          onClick={loadSettings}
          className="px-4 py-2 bg-gray-100 hover:bg-gray-200 rounded-xl font-bold text-gray-700 flex items-center gap-2 transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          刷新
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Left Column */}
        <div className="space-y-8">
          {/* Basic Settings */}
          <section className="space-y-4">
            <h3 className="text-lg font-extrabold text-gray-800 flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-gray-100 text-gray-600">
                    <Database className="w-4 h-4" />
                </div>
                基础设置
            </h3>

            <div className="ios-card rounded-[2rem] p-6 bg-white space-y-4">
              <div className="flex items-center justify-between p-4 bg-gray-50 rounded-xl">
                <div>
                  <div className="font-bold text-gray-900">允许用户注册</div>
                  <div className="text-xs text-gray-500 mt-1">开启后允许新用户注册账号</div>
                </div>
                <button
                  onClick={() => setSettings({...settings, registration_enabled: !settings.registration_enabled})}
                  className={`w-14 h-8 rounded-full transition-all relative ${
                    settings.registration_enabled ? 'bg-[#FFE815]' : 'bg-gray-300'
                  }`}
                >
                  <div
                    className={`w-6 h-6 bg-white rounded-full absolute top-1 transition-all shadow-md ${
                      settings.registration_enabled ? 'left-7' : 'left-1'
                    }`}
                  />
                </button>
              </div>

              <div className="flex items-center justify-between p-4 bg-gray-50 rounded-xl">
                <div>
                  <div className="font-bold text-gray-900">显示默认登录信息</div>
                  <div className="text-xs text-gray-500 mt-1">登录页面显示默认账号密码提示</div>
                </div>
                <button
                  onClick={() => setSettings({...settings, show_default_login_info: !settings.show_default_login_info})}
                  className={`w-14 h-8 rounded-full transition-all relative ${
                    settings.show_default_login_info ? 'bg-[#FFE815]' : 'bg-gray-300'
                  }`}
                >
                  <div
                    className={`w-6 h-6 bg-white rounded-full absolute top-1 transition-all shadow-md ${
                      settings.show_default_login_info ? 'left-7' : 'left-1'
                    }`}
                  />
                </button>
              </div>

              <div className="flex items-center justify-between p-4 bg-gray-50 rounded-xl">
                <div>
                  <div className="font-bold text-gray-900">登录滑动验证码</div>
                  <div className="text-xs text-gray-500 mt-1">开启后账号密码登录需要完成滑动验证</div>
                </div>
                <button
                  onClick={() => setSettings({...settings, login_captcha_enabled: !settings.login_captcha_enabled})}
                  className={`w-14 h-8 rounded-full transition-all relative ${
                    settings.login_captcha_enabled ? 'bg-[#FFE815]' : 'bg-gray-300'
                  }`}
                >
                  <div
                    className={`w-6 h-6 bg-white rounded-full absolute top-1 transition-all shadow-md ${
                      settings.login_captcha_enabled ? 'left-7' : 'left-1'
                    }`}
                  />
                </button>
              </div>

              <div className="flex items-center justify-between p-4 bg-gray-50 rounded-xl">
                <div>
                  <div className="font-bold text-gray-900">启用商品自动同步</div>
                  <div className="text-xs text-gray-500 mt-1">定时自动获取商品信息到本地数据库</div>
                </div>
                <button
                  onClick={() => setSettings({...settings, item_sync_enabled: !settings.item_sync_enabled})}
                  className={`w-14 h-8 rounded-full transition-all relative ${
                    settings.item_sync_enabled ? 'bg-[#FFE815]' : 'bg-gray-300'
                  }`}
                >
                  <div
                    className={`w-6 h-6 bg-white rounded-full absolute top-1 transition-all shadow-md ${
                      settings.item_sync_enabled ? 'left-7' : 'left-1'
                    }`}
                  />
                </button>
              </div>

              <div className="space-y-3 px-4">
                <label className="block text-sm font-bold text-gray-800">商品同步间隔（分钟）</label>
                <input
                  type="number"
                  value={Math.round((settings.item_sync_interval || 600) / 60)}
                  onChange={(e) => {
                    const minutes = parseInt(e.target.value) || 10;
                    setSettings({...settings, item_sync_interval: minutes * 60});
                  }}
                  className="w-full ios-input px-4 py-3 rounded-xl"
                  min="1"
                  max="1440"
                />
                <p className="text-xs text-gray-500">建议：10-60分钟</p>
              </div>

              <div className="space-y-3 px-4">
                <label className="block text-sm font-bold text-gray-800">每次最多同步页数</label>
                <input
                  type="number"
                  value={settings.item_sync_max_pages || 5}
                  onChange={(e) => setSettings({...settings, item_sync_max_pages: parseInt(e.target.value) || 5})}
                  className="w-full ios-input px-4 py-3 rounded-xl"
                  min="1"
                  max="50"
                />
                <p className="text-xs text-gray-500">每页20个商品</p>
              </div>
            </div>
          </section>

          {/* AI Configuration */}
          <section className="space-y-4">
            <h3 className="text-lg font-extrabold text-gray-800 flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-[#FFE815] text-black">
                    <Sparkles className="w-4 h-4" />
                </div>
                AI 智能回复配置
            </h3>

            <div className="ios-card rounded-[2rem] p-6 bg-white space-y-6">
              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">API 地址</label>
                <input
                  type="text"
                  value={settings.ai_api_url || 'https://dashscope.aliyuncs.com/compatible-mode/v1'}
                  onChange={e => setSettings({...settings, ai_api_url: e.target.value})}
                  className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                  placeholder="https://api.openai.com/v1"
                />
                <p className="text-xs text-gray-500">无需补全 /chat/completions</p>
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">API Key</label>
                <div className="relative">
                  <input
                    type={showApiKey ? 'text' : 'password'}
                    value={settings.ai_api_key || ''}
                    onChange={e => setSettings({...settings, ai_api_key: e.target.value})}
                    className="w-full ios-input px-4 py-3 pr-12 rounded-xl font-mono text-sm"
                    placeholder="sk-..."
                  />
                  <button
                    type="button"
                    onClick={() => setShowApiKey(!showApiKey)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 p-2 text-gray-400 hover:text-gray-600 transition-colors"
                  >
                    {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">模型</label>
                <select
                  value={settings.ai_model || 'qwen-plus'}
                  onChange={e => setSettings({...settings, ai_model: e.target.value})}
                  className="w-full ios-input px-4 py-3 rounded-xl"
                >
                  <option value="qwen-plus">通义千问 Plus</option>
                  <option value="qwen-turbo">通义千问 Turbo</option>
                  <option value="gpt-3.5-turbo">GPT-3.5 Turbo</option>
                  <option value="gpt-4">GPT-4</option>
                </select>
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">默认自动回复内容</label>
                <textarea
                  className="w-full ios-input px-4 py-3 rounded-xl min-h-[100px] text-sm resize-none"
                  value={settings.default_reply || ''}
                  onChange={e => setSettings({...settings, default_reply: e.target.value})}
                  placeholder="设置默认的自动回复内容..."
                ></textarea>
              </div>

              <div className="p-3 bg-amber-50 rounded-xl text-xs text-amber-700">
                <strong>常见 AI 服务:</strong>
                <ul className="list-disc list-inside mt-1 space-y-0.5">
                  <li>阿里云通义千问: https://dashscope.aliyuncs.com/compatible-mode/v1</li>
                  <li>OpenAI: https://api.openai.com/v1</li>
                </ul>
              </div>
            </div>
          </section>
        </div>

        {/* Right Column */}
        <div className="space-y-8">
          {/* SMTP Settings */}
          <section className="space-y-4">
            <h3 className="text-lg font-extrabold text-gray-800 flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-blue-100 text-blue-600">
                    <Mail className="w-4 h-4" />
                </div>
                SMTP 邮件配置
            </h3>

            <div className="ios-card rounded-[2rem] p-6 bg-white space-y-6">
              <p className="text-sm text-gray-500">配置SMTP服务器用于发送注册验证码等邮件通知</p>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-3">
                  <label className="block text-sm font-bold text-gray-800">SMTP服务器</label>
                  <input
                    type="text"
                    value={settings.smtp_server || ''}
                    onChange={e => setSettings({...settings, smtp_server: e.target.value})}
                    placeholder="smtp.qq.com"
                    className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                  />
                </div>
                <div className="space-y-3">
                  <label className="block text-sm font-bold text-gray-800">SMTP端口</label>
                  <input
                    type="number"
                    value={settings.smtp_port || 587}
                    onChange={e => setSettings({...settings, smtp_port: parseInt(e.target.value)})}
                    placeholder="587"
                    className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                  />
                </div>
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">发件邮箱</label>
                <input
                  type="email"
                  value={settings.smtp_user || ''}
                  onChange={e => setSettings({...settings, smtp_user: e.target.value})}
                  placeholder="your-email@qq.com"
                  className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                />
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">邮箱密码/授权码</label>
                <div className="relative">
                  <input
                    type={showSmtpPassword ? 'text' : 'password'}
                    value={settings.smtp_password || ''}
                    onChange={e => setSettings({...settings, smtp_password: e.target.value})}
                    placeholder="输入密码或授权码"
                    className="w-full ios-input px-4 py-3 pr-12 rounded-xl text-sm"
                  />
                  <button
                    type="button"
                    onClick={() => setShowSmtpPassword(!showSmtpPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 p-2 text-gray-400 hover:text-gray-600 transition-colors"
                  >
                    {showSmtpPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
                <p className="text-xs text-gray-500">QQ邮箱需要使用授权码</p>
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">发件人显示名（可选）</label>
                <input
                  type="text"
                  value={settings.smtp_from || ''}
                  onChange={e => setSettings({...settings, smtp_from: e.target.value})}
                  placeholder="闲鱼自动回复系统"
                  className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                />
              </div>
            </div>
          </section>
        </div>
      </div>

      {/* Online Update */}
      <section className="space-y-4">
        <h3 className="text-lg font-extrabold text-gray-800 flex items-center gap-2">
          <div className="p-1.5 rounded-lg bg-emerald-100 text-emerald-600">
            <Download className="w-4 h-4" />
          </div>
          在线更新
        </h3>

        <div className="ios-card rounded-[2rem] p-6 bg-white space-y-6">
          <p className="text-sm text-gray-500">
            从指定 GitHub 仓库检查并拉取最新代码。仅管理员可用，更新后建议重启服务生效。
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-3">
              <label className="block text-sm font-bold text-gray-800 flex items-center gap-1.5">
                <GitBranch className="w-4 h-4 text-gray-400" /> 更新仓库 (owner/name)
              </label>
              <input
                type="text"
                value={settings.github_repo || 'ByebyDoggy/xianyu-super-butler'}
                onChange={e => setSettings({ ...settings, github_repo: e.target.value })}
                placeholder="owner/repo"
                className="w-full ios-input px-4 py-3 rounded-xl font-mono text-sm"
              />
            </div>
            <div className="space-y-3">
              <label className="block text-sm font-bold text-gray-800">更新分支</label>
              <input
                type="text"
                value={settings.github_branch || 'main'}
                onChange={e => setSettings({ ...settings, github_branch: e.target.value })}
                placeholder="main"
                className="w-full ios-input px-4 py-3 rounded-xl font-mono text-sm"
              />
            </div>
          </div>

          <div className="space-y-3">
            <label className="block text-sm font-bold text-gray-800">GitHub Token（可选，私有仓库或提升限流）</label>
            <div className="relative">
              <input
                type={showGithubToken ? 'text' : 'password'}
                value={settings.github_token || ''}
                onChange={e => setSettings({ ...settings, github_token: e.target.value })}
                placeholder="ghp_...（留空表示公开仓库）"
                className="w-full ios-input px-4 py-3 pr-12 rounded-xl font-mono text-sm"
              />
              <button
                type="button"
                onClick={() => setShowGithubToken(!showGithubToken)}
                className="absolute right-3 top-1/2 -translate-y-1/2 p-2 text-gray-400 hover:text-gray-600 transition-colors"
              >
                {showGithubToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            <p className="text-xs text-gray-500">保存配置后再点击「检查更新」</p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={handleCheckUpdate}
              disabled={checkingUpdate}
              className="px-5 py-3 bg-gray-100 hover:bg-gray-200 rounded-xl font-bold text-gray-700 flex items-center gap-2 transition-colors disabled:opacity-70"
            >
              <RefreshCw className={`w-4 h-4 ${checkingUpdate ? 'animate-spin' : ''}`} />
              {checkingUpdate ? '检查中...' : '检查更新'}
            </button>
            <button
              type="button"
              onClick={handleApplyUpdate}
              disabled={applyingUpdate}
              className="px-5 py-3 ios-btn-primary rounded-xl font-bold flex items-center gap-2 disabled:opacity-70"
            >
              <Download className="w-4 h-4" />
              {applyingUpdate ? '更新中...' : '立即更新'}
            </button>
            <label className="flex items-center gap-2 text-sm text-gray-600 font-medium select-none">
              <input
                type="checkbox"
                checked={restartAfterUpdate}
                onChange={e => setRestartAfterUpdate(e.target.checked)}
                className="w-4 h-4 rounded"
              />
              更新后自动重启
            </label>
          </div>

          {updateError && (
            <div className="p-3 bg-red-50 rounded-xl text-sm text-red-600 font-medium flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" /> {updateError}
            </div>
          )}

          {updateInfo && (
            <div className="p-4 bg-gray-50 rounded-xl space-y-2 text-sm">
              <div className="flex items-center gap-2 font-bold text-gray-900">
                {updateInfo.update_available === true && (
                  <><AlertTriangle className="w-4 h-4 text-amber-500" /> 发现新版本</>
                )}
                {updateInfo.update_available === false && (
                  <><CheckCircle2 className="w-4 h-4 text-emerald-500" /> 已是最新版本</>
                )}
                {updateInfo.update_available == null && (
                  <><GitBranch className="w-4 h-4 text-gray-400" /> 非 Git 部署，无法比对提交号</>
                )}
              </div>
              <div className="text-gray-600">当前版本：{updateInfo.current_version}
                {updateInfo.current_commit ? ` (${updateInfo.current_commit.slice(0, 8)})` : ''}
              </div>
              <div className="text-gray-600">远端提交：{(updateInfo.latest_commit || '').slice(0, 8)} {updateInfo.latest_message}</div>
              {updateInfo.latest_author && (
                <div className="text-gray-400 text-xs">
                  提交者 {updateInfo.latest_author} · {updateInfo.latest_date}
                </div>
              )}
              {updateInfo.html_url && (
                <a href={updateInfo.html_url} target="_blank" rel="noreferrer" className="text-xs text-blue-500 hover:underline">
                  查看提交详情
                </a>
              )}
            </div>
          )}
        </div>
      </section>

      {/* Save Button */}
      <div className="fixed bottom-10 right-10 z-30">
        <button
            onClick={handleSave}
            disabled={saving}
            className="ios-btn-primary px-10 py-5 rounded-[2rem] text-lg shadow-2xl shadow-yellow-200 flex items-center gap-3 transform hover:scale-105 active:scale-95 transition-all disabled:opacity-70"
        >
            <Save className="w-6 h-6" />
            {saving ? '保存中...' : '保存所有配置'}
        </button>
      </div>
    </div>
  );
};

export default Settings;
