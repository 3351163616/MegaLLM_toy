"""
浏览器自动化处理模块
用于绕过 Vercel Security Checkpoint 等安全验证
使用 Patchright (Playwright 的反检测版本)
"""

try:
    # 优先使用 Patchright (Playwright 的反检测版本)
    from patchright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    USING_PATCHRIGHT = True
    print("✅ 使用 Patchright (Playwright 反检测版本)")
except ImportError:
    # 回退到标准 playwright
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
        USING_PATCHRIGHT = False
        print("⚠️  使用标准 Playwright (建议安装 Patchright: pip install patchright)")
    except ImportError:
        print("❌ 未安装 Playwright 或 Patchright!")
        print("   请运行: pip install patchright")
        raise

import time
import json
import random


class BrowserSession:
    """处理需要浏览器验证的会话"""

    def __init__(self, config):
        self.config = config
        self.clash_proxy = config['clash']['local_proxy']
        self.api_base = config.get('api_base', 'https://megallm.io')
        self.headless = config.get('browser', {}).get('headless', True)
        self.timeout = config.get('browser', {}).get('timeout', 30000)

    def get_verified_session(self, proxy_name=None):
        """
        通过浏览器完成验证并返回 cookies

        Args:
            proxy_name: 可选，使用的代理节点名称（用于日志）

        Returns:
            dict: 验证后的 cookies 字典
        """
        log_prefix = f"[{proxy_name}] " if proxy_name else ""
        print(f"{log_prefix}🌐 启动浏览器进行安全验证...")

        with sync_playwright() as p:
            browser = None
            try:
                # 启动浏览器 - Patchright 已内置最佳反检测配置
                # 只需要最基本的参数，Patchright 会自动处理其他的
                browser = p.chromium.launch(
                    headless=self.headless,  # 支持 headless 模式（Patchright 已修复检测）
                    proxy={"server": self.clash_proxy}
                    # Patchright 自动添加的参数:
                    # - 移除 --enable-automation
                    # - 移除 --disable-popup-blocking
                    # - 移除 --disable-component-update
                    # - 移除 --disable-default-apps
                    # - 移除 --disable-extensions
                    # - 添加 --disable-blink-features=AutomationControlled
                )

                # 随机User-Agent列表 (模拟不同的浏览器)
                user_agents = [
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                ]
                selected_ua = random.choice(user_agents)

                # 创建浏览器上下文
                context = browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent=selected_ua,
                    locale='en-US',
                    timezone_id='America/New_York'
                )

                # Patchright 已经内置了所有必要的反检测补丁
                # 不需要手动注入脚本！
                # Patchright 自动处理:
                # - Runtime.enable Leak (最重要的补丁)
                # - Console.enable Leak
                # - navigator.webdriver 修复
                # - Command Flags 优化
                # - 所有 Playwright 特征隐藏

                # 可选: 添加额外的自定义脚本（如果需要）
                # context.add_init_script("""
                #     // 你的自定义脚本
                # """)

                page = context.new_page()

                print(f"{log_prefix}📡 正在访问 {self.api_base}...")

                # 访问首页触发验证
                page.goto(self.api_base, wait_until='networkidle', timeout=self.timeout)

                print(f"{log_prefix}⏳ 等待 Vercel Security Checkpoint 验证...")
                print(f"{log_prefix}   (这可能需要 10-30 秒，请耐心等待)")

                # 等待验证完成的多种策略
                verified = False
                start_time = time.time()
                max_wait = 60  # 增加到 60 秒等待时间

                while time.time() - start_time < max_wait:
                    current_url = page.url
                    current_title = page.title()

                    # 打印当前状态 (每5秒)
                    elapsed = int(time.time() - start_time)
                    if elapsed % 5 == 0 and elapsed > 0:
                        print(f"{log_prefix}   ⏱️  已等待 {elapsed} 秒... (URL: {current_url[:50]}...)")

                    # 检查页面标题是否不再是 checkpoint
                    if 'checkpoint' not in current_title.lower() and 'verifying' not in current_title.lower():
                        print(f"{log_prefix}✅ 安全验证通过！(标题: {current_title})")
                        verified = True
                        break

                    # 检查 URL 是否已经跳转
                    if 'checkpoint' not in current_url.lower():
                        print(f"{log_prefix}✅ 安全验证通过！(URL 已跳转)")
                        verified = True
                        break

                    # 检查页面内容
                    try:
                        body_text = page.text_content('body')

                        # 如果页面内容包含正常内容 (不是验证页面)
                        if body_text:
                            if 'verifying your browser' not in body_text.lower() and \
                               'security checkpoint' not in body_text.lower() and \
                               len(body_text) > 500:  # 正常页面内容应该更长
                                print(f"{log_prefix}✅ 页面内容验证通过！")
                                verified = True
                                break
                    except:
                        pass

                    # 等待一小段时间再检查
                    time.sleep(1)

                if not verified:
                    print(f"{log_prefix}❌ 验证超时 (等待了 {int(time.time() - start_time)} 秒)")
                    print(f"{log_prefix}   当前标题: {page.title()}")
                    print(f"{log_prefix}   当前 URL: {page.url}")
                    print(f"{log_prefix}   这说明 Vercel 仍然检测到了自动化特征！")
                    return {}

                # 额外等待确保所有 cookies 设置完成
                print(f"{log_prefix}⏱️  等待 cookies 设置完成...")
                time.sleep(3)

                # 尝试多次获取 cookies，有时需要等待
                cookies = []
                max_cookie_retries = 3
                for attempt in range(max_cookie_retries):
                    cookies = context.cookies()
                    if cookies:
                        break

                    if attempt < max_cookie_retries - 1:
                        print(f"{log_prefix}⏳ Cookie 获取尝试 {attempt + 1}/{max_cookie_retries} 失败，等待后重试...")
                        time.sleep(2)

                if not cookies:
                    print(f"{log_prefix}⚠️  未获取到任何 cookies (尝试了 {max_cookie_retries} 次)")

                    # 调试: 打印当前 URL 和页面标题
                    try:
                        print(f"{log_prefix}🔍 调试信息:")
                        print(f"{log_prefix}   当前 URL: {page.url}")
                        print(f"{log_prefix}   页面标题: {page.title()}")
                    except:
                        pass

                    return {}

                # 转换为 requests 可用格式
                session_cookies = {c['name']: c['value'] for c in cookies}

                print(f"{log_prefix}🍪 成功获取 {len(session_cookies)} 个 cookies")

                # 打印关键 cookies（调试用）
                important_cookies = ['_vercel_jwt', '__vercel_live_token', 'vercel-checkpoint']
                for key in important_cookies:
                    if key in session_cookies:
                        print(f"{log_prefix}   - {key}: {session_cookies[key][:20]}...")

                # 验证 cookies 是否真的有效（访问一个简单的 API 端点测试）
                try:
                    print(f"{log_prefix}🧪 验证 cookies 有效性...")

                    # 先访问 session 端点
                    response = page.goto(f"{self.api_base}/api/auth/session", wait_until='domcontentloaded', timeout=10000)

                    # 检查是否被重定向回 checkpoint
                    if response and 'checkpoint' not in page.url.lower():
                        print(f"{log_prefix}✅ Cookies 通过 session 端点验证")

                        # 再访问 csrf 端点，确保可以正常调用 API
                        try:
                            csrf_response = page.goto(f"{self.api_base}/api/auth/csrf", wait_until='domcontentloaded', timeout=10000)
                            if csrf_response and csrf_response.status == 200:
                                print(f"{log_prefix}✅ Cookies 通过 CSRF 端点验证")
                            else:
                                print(f"{log_prefix}⚠️  CSRF 端点访问异常: {csrf_response.status if csrf_response else 'None'}")
                        except Exception as e:
                            print(f"{log_prefix}⚠️  CSRF 端点验证失败: {e}")

                    else:
                        print(f"{log_prefix}⚠️  Cookies 可能无效（被重定向到 checkpoint）")
                except Exception as e:
                    print(f"{log_prefix}⚠️  Cookies 验证失败: {e}")
                    # 继续返回 cookies，让后续流程决定是否可用

                return session_cookies

            except PlaywrightTimeout as e:
                print(f"{log_prefix}❌ 浏览器操作超时: {e}")
                return {}
            except Exception as e:
                print(f"{log_prefix}❌ 浏览器验证失败: {e}")
                import traceback
                traceback.print_exc()
                return {}
            finally:
                if browser:
                    browser.close()

    def test_cookies(self, cookies):
        """
        测试 cookies 是否有效

        Args:
            cookies: cookies 字典

        Returns:
            bool: cookies 是否有效
        """
        if not cookies:
            return False

        print("🧪 测试 cookies 有效性...")

        with sync_playwright() as p:
            browser = None
            try:
                browser = p.chromium.launch(
                    headless=True,
                    proxy={"server": self.clash_proxy}
                )

                context = browser.new_context()

                # 设置 cookies
                cookie_list = [
                    {
                        'name': name,
                        'value': value,
                        'domain': self.api_base.replace('https://', '').replace('http://', ''),
                        'path': '/'
                    }
                    for name, value in cookies.items()
                ]
                context.add_cookies(cookie_list)

                page = context.new_page()

                # 访问页面
                page.goto(self.api_base, wait_until='domcontentloaded', timeout=10000)

                # 检查是否被重定向到 checkpoint
                if 'checkpoint' in page.url.lower():
                    print("❌ Cookies 已失效（被重定向到验证页面）")
                    return False

                print("✅ Cookies 有效")
                return True

            except Exception as e:
                print(f"❌ Cookies 测试失败: {e}")
                return False
            finally:
                if browser:
                    browser.close()


class CookieManager:
    """Cookie 管理器，用于缓存和复用 cookies"""

    def __init__(self, cache_file='browser_cookies.json'):
        import threading
        self.cache_file = cache_file
        self.cookies_cache = self._load_cache()
        self.lock = threading.Lock()  # 添加线程锁防止并发冲突

    def _load_cache(self):
        """从文件加载缓存的 cookies"""
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_cache(self):
        """保存 cookies 到文件"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cookies_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️  保存 cookies 缓存失败: {e}")

    def get_cookies(self, proxy_name=None):
        """获取指定代理的 cookies"""
        with self.lock:
            key = proxy_name or 'default'
            return self.cookies_cache.get(key, {})

    def set_cookies(self, cookies, proxy_name=None):
        """设置指定代理的 cookies"""
        with self.lock:
            key = proxy_name or 'default'
            self.cookies_cache[key] = {
                'cookies': cookies,
                'timestamp': time.time()
            }
            self._save_cache()

    def is_expired(self, proxy_name=None, max_age=3600):
        """检查 cookies 是否过期（默认 1 小时）"""
        with self.lock:
            key = proxy_name or 'default'
            if key not in self.cookies_cache:
                return True

            cache_data = self.cookies_cache[key]
            age = time.time() - cache_data.get('timestamp', 0)
            return age > max_age

    def clear_cookies(self, proxy_name=None):
        """清除指定代理的 cookies"""
        with self.lock:
            key = proxy_name or 'default'
            if key in self.cookies_cache:
                del self.cookies_cache[key]
                self._save_cache()


if __name__ == '__main__':
    # 测试代码
    import sys

    test_config = {
        'api_base': 'https://megallm.io',
        'clash': {
            'local_proxy': 'http://127.0.0.1:7897'
        },
        'browser': {
            'headless': False,  # 测试时显示浏览器
            'timeout': 30000
        }
    }

    print("=" * 50)
    print("浏览器验证模块测试")
    print("=" * 50)

    browser_session = BrowserSession(test_config)
    cookies = browser_session.get_verified_session()

    if cookies:
        print(f"\n✅ 成功获取 cookies: {list(cookies.keys())}")

        # 测试 cookies 有效性
        is_valid = browser_session.test_cookies(cookies)
        print(f"\nCookies 有效性: {'✅ 有效' if is_valid else '❌ 无效'}")
    else:
        print("\n❌ 未能获取 cookies")
        sys.exit(1)
