"""
刮刮乐远程控制 API 路由
提供 WebSocket 和 HTTP 接口用于远程操作滑块验证

安全说明：
- 所有 HTTP/WebSocket 入口都需要登录会话 Cookie 或会话级 access_token 才能访问；
- access_token 由 create_session 生成，并随控制页面 URL 一起下发，
  避免仅凭可猜测的 session_id 访问他人验证会话（截图/鼠标控制）。
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional
import asyncio
import json
import os
from loguru import logger

from utils.captcha_remote_control import captcha_controller


# 创建路由器
router = APIRouter(prefix="/api/captcha", tags=["captcha"])


class MouseEvent(BaseModel):
    """鼠标事件模型"""
    session_id: str
    event_type: str  # down, move, up
    x: int
    y: int


class SessionCheckRequest(BaseModel):
    """会话检查请求"""
    session_id: str


# =============================================================================
# 鉴权
# =============================================================================

def _authenticated_user(request: Request):
    """从会话 Cookie 解析当前登录用户（延迟导入避免循环依赖）"""
    try:
        from reply_server import get_current_user_from_session_cookie, SESSION_COOKIE_NAME
        session_id = request.cookies.get(SESSION_COOKIE_NAME)
        return get_current_user_from_session_cookie(session_id)
    except Exception:
        return None


def require_auth(request: Request):
    """要求登录会话"""
    user = _authenticated_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未授权访问")
    return user


def _authorize_session(request: Request, session_id: str, token: Optional[str] = None):
    """只允许持有该会话 access_token 的远程控制页面，或管理员访问。

    这样既保留了“手机扫码远程控制”的使用方式，又防止其它登录用户
    仅凭可猜测的 session_id 访问他人验证会话（截图/鼠标控制）。
    """
    expected = captcha_controller.get_access_token(session_id)
    if token and expected and token == expected:
        return {"access_token": True}

    user = _authenticated_user(request)
    if user and user.get('is_admin'):
        return user

    raise HTTPException(status_code=403, detail="无权访问该验证会话")


# =============================================================================
# WebSocket 端点 - 实时通信
# =============================================================================

@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    WebSocket 连接用于实时传输截图和接收鼠标事件
    """
    # 鉴权：会话 access_token 或管理员登录
    token = websocket.query_params.get("token")
    authorized = False
    expected = captcha_controller.get_access_token(session_id)
    if token and expected and token == expected:
        authorized = True
    else:
        try:
            from reply_server import get_current_user_from_session_cookie, SESSION_COOKIE_NAME
            user = get_current_user_from_session_cookie(websocket.cookies.get(SESSION_COOKIE_NAME))
            if user and user.get('is_admin'):
                authorized = True
        except Exception:
            pass
    if not authorized:
        await websocket.close(code=4401)
        logger.warning(f"拒绝未授权的验证 WebSocket 连接: {session_id}")
        return

    await websocket.accept()
    logger.info(f"🔌 WebSocket 连接建立: {session_id}")
    
    # 注册 WebSocket 连接
    captcha_controller.websocket_connections[session_id] = websocket
    
    try:
        # 发送初始会话信息
        if session_id in captcha_controller.active_sessions:
            session_data = captcha_controller.active_sessions[session_id]
            await websocket.send_json({
                'type': 'session_info',
                'screenshot': session_data['screenshot'],
                'captcha_info': session_data['captcha_info'],
                'viewport': session_data['viewport']
            })
        else:
            await websocket.send_json({
                'type': 'error',
                'message': '会话不存在'
            })
            await websocket.close()
            return
        
        # 持续接收客户端消息
        while True:
            data = await websocket.receive_json()
            msg_type = data.get('type')
            
            if msg_type == 'mouse_event':
                # 处理鼠标事件
                event_type = data.get('event_type')
                x = data.get('x')
                y = data.get('y')
                
                success = await captcha_controller.handle_mouse_event(
                    session_id, event_type, x, y
                )
                
                if success:
                    # 只在鼠标释放后才检查完成状态
                    if event_type == 'up':
                        # 等待页面更新（给验证码一些反应时间）
                        await asyncio.sleep(1.0)
                        
                        # 多次确认滑块确实消失
                        completed = await captcha_controller.check_completion(session_id)
                        
                        if completed:
                            # 再次确认（避免误判）
                            await asyncio.sleep(0.5)
                            completed = await captcha_controller.check_completion(session_id)
                        
                        if completed:
                            await websocket.send_json({
                                'type': 'completed',
                                'message': '验证成功！'
                            })
                            logger.success(f"✅ 验证完成: {session_id}")
                            break
                        else:
                            # 更新截图显示验证结果
                            screenshot = await captcha_controller.update_screenshot(session_id)
                            if screenshot:
                                await websocket.send_json({
                                    'type': 'screenshot_update',
                                    'screenshot': screenshot
                                })
                    else:
                        # 按下或移动时，实时更新截图（截取整个验证码容器）
                        if event_type in ['down', 'move']:
                            # 截取整个验证码容器，降低质量换取速度
                            screenshot = await captcha_controller.update_screenshot(session_id, quality=30)
                            if screenshot:
                                await websocket.send_json({
                                    'type': 'screenshot_update',
                                    'screenshot': screenshot
                                })
            
            elif msg_type == 'check_completion':
                # 手动检查完成状态
                completed = await captcha_controller.check_completion(session_id)
                await websocket.send_json({
                    'type': 'completion_status',
                    'completed': completed
                })
                
                if completed:
                    break
            
            elif msg_type == 'ping':
                # 心跳
                await websocket.send_json({'type': 'pong'})
    
    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket 连接断开: {session_id}")
    
    except Exception as e:
        logger.error(f"❌ WebSocket 错误: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    finally:
        # 清理
        if session_id in captcha_controller.websocket_connections:
            del captcha_controller.websocket_connections[session_id]
        
        logger.info(f"🔒 WebSocket 会话结束: {session_id}")


# =============================================================================
# HTTP 端点 - REST API
# =============================================================================

@router.get("/sessions")
async def get_active_sessions(request: Request):
    """获取所有活跃的验证会话（需要登录）"""
    require_auth(request)
    sessions = []
    for session_id, data in captcha_controller.active_sessions.items():
        sessions.append({
            'session_id': session_id,
            'completed': data.get('completed', False),
            'has_websocket': session_id in captcha_controller.websocket_connections
        })
    
    return {
        'count': len(sessions),
        'sessions': sessions
    }


@router.get("/session/{session_id}")
async def get_session_info(session_id: str, request: Request, token: Optional[str] = Query(default=None)):
    """获取指定会话的信息"""
    _authorize_session(request, session_id, token)

    if session_id not in captcha_controller.active_sessions:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    session_data = captcha_controller.active_sessions[session_id]
    
    return {
        'session_id': session_id,
        'screenshot': session_data['screenshot'],
        'captcha_info': session_data['captcha_info'],
        'viewport': session_data['viewport'],
        'completed': session_data.get('completed', False)
    }


@router.get("/screenshot/{session_id}")
async def get_screenshot(session_id: str, request: Request, token: Optional[str] = Query(default=None)):
    """获取最新截图"""
    _authorize_session(request, session_id, token)

    screenshot = await captcha_controller.update_screenshot(session_id)
    
    if not screenshot:
        raise HTTPException(status_code=404, detail="无法获取截图")
    
    return {'screenshot': screenshot}


@router.post("/mouse_event")
async def handle_mouse_event(event: MouseEvent, request: Request, token: Optional[str] = Query(default=None)):
    """处理鼠标事件（HTTP方式，不推荐，建议使用WebSocket）"""
    _authorize_session(request, event.session_id, token)

    success = await captcha_controller.handle_mouse_event(
        event.session_id,
        event.event_type,
        event.x,
        event.y
    )
    
    if not success:
        raise HTTPException(status_code=400, detail="处理失败")
    
    # 检查是否完成
    completed = await captcha_controller.check_completion(event.session_id)
    
    return {
        'success': True,
        'completed': completed
    }


@router.post("/check_completion")
async def check_completion(request_body: SessionCheckRequest, request: Request, token: Optional[str] = Query(default=None)):
    """检查验证是否完成"""
    _authorize_session(request, request_body.session_id, token)

    completed = await captcha_controller.check_completion(request_body.session_id)
    
    return {
        'session_id': request_body.session_id,
        'completed': completed
    }


@router.delete("/session/{session_id}")
async def close_session(session_id: str, request: Request, token: Optional[str] = Query(default=None)):
    """关闭会话"""
    _authorize_session(request, session_id, token)
    await captcha_controller.close_session(session_id)
    return {'success': True}


# =============================================================================
# 前端页面
# =============================================================================

@router.get("/status/{session_id}")
async def get_captcha_status(session_id: str, request: Request, token: Optional[str] = Query(default=None)):
    """
    获取验证状态
    用于前端轮询检查验证是否完成
    """
    try:
        _authorize_session(request, session_id, token)
        is_completed = captcha_controller.is_completed(session_id)
        session_exists = captcha_controller.session_exists(session_id)
        
        return {
            "success": True,
            "completed": is_completed,
            "session_exists": session_exists,
            "session_id": session_id
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取验证状态失败: {e}")
        return {
            "success": False,
            "completed": False,
            "session_exists": False,
            "session_id": session_id,
            "error": str(e)
        }


def _inject_bootstrap(html_content: str, session_id: Optional[str], token: Optional[str]) -> str:
    """将初始会话 ID / token 安全地注入页面（JSON 编码，防止 XSS）。"""
    if not session_id:
        return html_content

    def _js_string(value: str) -> str:
        # JSON 编码并转义 < > &，避免提前闭合 </script>
        encoded = json.dumps(value)
        return encoded.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')

    bootstrap = (
        "<script>"
        f"window.INITIAL_SESSION_ID = {_js_string(session_id)};"
        f"window.INITIAL_SESSION_TOKEN = {_js_string(token or '')};"
        "</script>"
    )
    return html_content.replace('</body>', bootstrap + '</body>')


@router.get("/control", response_class=HTMLResponse)
async def captcha_control_page(request: Request):
    """返回滑块控制页面（需要登录）"""
    require_auth(request)
    html_file = "captcha_control.html"
    
    if os.path.exists(html_file):
        return FileResponse(html_file, media_type="text/html")
    else:
        # 返回简单的提示页面
        return HTMLResponse(content="""
        <!DOCTYPE html>
        <html>
        <head>
            <title>验证码控制面板</title>
        </head>
        <body>
            <h1>验证码控制面板</h1>
            <p>前端页面文件 captcha_control.html 不存在</p>
            <p>请查看文档了解如何创建前端页面</p>
        </body>
        </html>
        """)


@router.get("/control/{session_id}", response_class=HTMLResponse)
async def captcha_control_page_with_session(
    session_id: str,
    request: Request,
    token: Optional[str] = Query(default=None),
):
    """返回带会话ID的滑块控制页面（需要登录或持有 access_token）"""
    _authorize_session(request, session_id, token)

    html_file = "captcha_control.html"
    
    if os.path.exists(html_file):
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        return HTMLResponse(content=_inject_bootstrap(html_content, session_id, token))
    else:
        raise HTTPException(status_code=404, detail="前端页面不存在")
