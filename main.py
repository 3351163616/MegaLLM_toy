import json
import requests
import random
import string
import time
import urllib.parse
import threading
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from browser_handler import BrowserSession, CookieManager


class ProxyPool:
    """代理池管理类 - 基于 Clash API"""

    PROXY_STATE_FILE = 'proxy_pool_state.json'

    def __init__(self, config):
        self.config = config
        self.clash_api = config['clash']['api_url']
        self.clash_secret = config['clash']['secret']
        self.local_proxy = config['clash']['local_proxy']
        self.max_failures = config['proxy_pool']['max_failures']
        self.test_url = config['proxy_pool']['test_url']

        self.proxies_dict = {
            'http': self.local_proxy,
            'https': self.local_proxy
        }

        # 节点列表和状态
        self.all_proxies = []  # 所有节点名称列表
        self.active_proxies = []  # 可用节点列表
        self.failed_proxies = {}  # 失效节点: {name: failure_count}
        self.lock = threading.Lock()  # 线程锁

        # 加载节点和状态
        self.load_proxies_from_clash_api()
        self.load_state()

        print(f"\n代理池初始化完成:")
        print(f"  总节点数: {len(self.all_proxies)}")
        print(f"  可用节点: {len(self.active_proxies)}")
        print(f"  失效节点: {len(self.failed_proxies)}")

    def load_proxies_from_clash_api(self):
        """从 Clash API 加载节点列表"""
        try:
            headers = {'Authorization': f'Bearer {self.clash_secret}'}

            # 获取所有代理信息
            response = requests.get(f"{self.clash_api}/proxies", headers=headers, timeout=10)

            if response.status_code == 200:
                data = response.json()
                proxies_data = data.get('proxies', {})

                # 获取GLOBAL选择器组的所有可用节点
                global_proxy = proxies_data.get('GLOBAL', {})
                available_nodes = global_proxy.get('all', [])

                if available_nodes:
                    # 过滤掉特殊节点（如DIRECT, REJECT等）和统计信息节点
                    self.all_proxies = [
                        node for node in available_nodes
                        if node not in ['DIRECT', 'REJECT', 'GLOBAL', 'Proxy']
                        and not node.startswith('剩余流量')
                        and not node.startswith('套餐到期')
                    ]
                    print(f"✓ 从 Clash API 加载了 {len(self.all_proxies)} 个节点")
                else:
                    print("⚠ Clash API 未返回可用节点")
                    self.all_proxies = []
            else:
                print(f"⚠ Clash API 请求失败，状态码: {response.status_code}")
                self.all_proxies = []

        except Exception as e:
            print(f"⚠ 从 Clash API 加载节点失败: {e}")
            self.all_proxies = []

    def load_state(self):
        """加载代理池状态"""
        if os.path.exists(self.PROXY_STATE_FILE):
            try:
                with open(self.PROXY_STATE_FILE, 'r', encoding='utf-8') as f:
                    state = json.load(f)

                self.failed_proxies = state.get('failed_proxies', {})

                # 计算可用节点（排除失效节点）
                self.active_proxies = [
                    p for p in self.all_proxies
                    if p not in self.failed_proxies
                ]

                print(f"✓ 已从本地加载代理池状态")
            except Exception as e:
                print(f"⚠ 加载代理池状态失败: {e}")
                self.active_proxies = self.all_proxies.copy()
        else:
            # 首次运行，所有节点都是可用的
            self.active_proxies = self.all_proxies.copy()

    def save_state(self):
        """保存代理池状态"""
        try:
            state = {
                'failed_proxies': self.failed_proxies,
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            with open(self.PROXY_STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠ 保存代理池状态失败: {e}")

    def switch_proxy(self, proxy_name):
        """通过 Clash API 切换到指定节点"""
        try:
            headers = {'Authorization': f'Bearer {self.clash_secret}'}

            # Clash API 切换节点的端点
            # 首先需要获取默认的选择器组名（通常是 "GLOBAL" 或 "Proxy"）
            selector_url = f"{self.clash_api}/proxies/GLOBAL"

            # 切换节点
            switch_url = f"{self.clash_api}/proxies/GLOBAL"
            payload = {'name': proxy_name}

            response = requests.put(switch_url, headers=headers, json=payload, timeout=5)

            if response.status_code == 204:
                print(f"✓ 已切换到节点: {proxy_name}")
                return True
            else:
                print(f"⚠ 切换节点失败 [{response.status_code}]: {proxy_name}")
                return False

        except Exception as e:
            print(f"⚠ 切换节点异常: {e}")
            return False

    def get_next_proxy(self):
        """随机获取下一个可用代理"""
        with self.lock:
            if not self.active_proxies:
                print("⚠ 没有可用节点！")
                return None

            # 随机选择
            proxy_name = random.choice(self.active_proxies)

            # 切换到该节点
            if self.switch_proxy(proxy_name):
                return proxy_name
            else:
                # 切换失败，标记失败并尝试下一个
                self.mark_proxy_failed(proxy_name)
                return self.get_next_proxy()  # 递归尝试下一个

    def mark_proxy_failed(self, proxy_name):
        """标记代理失败"""
        with self.lock:
            if proxy_name not in self.failed_proxies:
                self.failed_proxies[proxy_name] = 0

            self.failed_proxies[proxy_name] += 1

            print(f"⚠ 节点 {proxy_name} 失败次数: {self.failed_proxies[proxy_name]}/{self.max_failures}")

            # 如果超过最大失败次数，从活跃列表中移除
            if self.failed_proxies[proxy_name] >= self.max_failures:
                if proxy_name in self.active_proxies:
                    self.active_proxies.remove(proxy_name)
                    print(f"✗ 节点 {proxy_name} 已从活跃列表移除")
                    self.save_state()

    def check_proxy_health(self, proxy_name):
        """检查单个代理的健康状态"""
        try:
            # 切换到指定节点
            if not self.switch_proxy(proxy_name):
                return False

            # 等待切换生效
            time.sleep(1)

            # 测试连接
            response = requests.get(
                self.test_url,
                proxies=self.proxies_dict,
                timeout=10
            )

            if response.status_code in [200, 204]:
                print(f"✓ 节点 {proxy_name} 健康检查通过")
                return True
            else:
                print(f"✗ 节点 {proxy_name} 健康检查失败 [状态码: {response.status_code}]")
                return False

        except Exception as e:
            print(f"✗ 节点 {proxy_name} 健康检查异常: {e}")
            return False

    def health_check_all(self):
        """并发检查所有代理的健康状态"""
        print("\n开始代理池健康检查(并发模式)...")

        recovered = []  # 恢复的节点
        all_nodes_to_check = self.active_proxies.copy() + list(self.failed_proxies.keys())

        if not all_nodes_to_check:
            print("没有需要检查的节点")
            return

        # 并发检查所有节点
        with ThreadPoolExecutor(max_workers=20) as executor:
            future_to_proxy = {
                executor.submit(self.check_proxy_health, proxy_name): proxy_name
                for proxy_name in all_nodes_to_check
            }

            for future in as_completed(future_to_proxy):
                proxy_name = future_to_proxy[future]
                try:
                    is_healthy = future.result()

                    # 如果是活跃节点但检查失败
                    if proxy_name in self.active_proxies and not is_healthy:
                        self.mark_proxy_failed(proxy_name)

                    # 如果是失效节点但检查通过,恢复它
                    elif proxy_name in self.failed_proxies and is_healthy:
                        with self.lock:
                            del self.failed_proxies[proxy_name]
                            if proxy_name not in self.active_proxies:
                                self.active_proxies.append(proxy_name)
                                recovered.append(proxy_name)

                except Exception as e:
                    print(f"✗ 检查节点 {proxy_name} 时发生异常: {e}")

        if recovered:
            print(f"\n✓ 恢复节点: {', '.join(recovered)}")

        self.save_state()
        print(f"\n健康检查完成 - 可用节点: {len(self.active_proxies)}/{len(self.all_proxies)}")

    def get_proxies_dict(self):
        """获取代理字典（用于 requests）"""
        return self.proxies_dict


def load_config():
    """加载配置文件"""
    with open('config.json', 'r', encoding='utf-8') as f:
        return json.load(f)


def get_temp_email(config, proxies=None):
    """获取临时邮箱地址"""
    email_base = config['email_base']
    api_url = f"{email_base}/api/generate-email"

    headers = {
        'Referer': f"{email_base}/"
    }

    try:
        response = requests.get(api_url, headers=headers, proxies=proxies)
        response.raise_for_status()
        data = response.json()
        
        if 'email' in data:
            email = data['email']
            print(f"成功获取临时邮箱: {email}")
            return email
        else:
            print("响应中未找到email字段")
            return None
            
    except requests.RequestException as e:
        print(f"请求失败: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"JSON解析失败: {e}")
        return None


def generate_name():
    """随机生成人名"""
    first_names = ["James", "John", "Robert", "Michael", "William", "David", "Richard", "Joseph", "Thomas", "Charles",
                   "Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara", "Susan", "Jessica", "Sarah", "Karen"]
    last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
                  "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin"]
    return f"{random.choice(first_names)} {random.choice(last_names)}"


def generate_password():
    """随机生成密码（10-14位，包含大小写字母、数字、特殊字符，特殊字符占比更多）"""
    length = random.randint(10, 14)
    special_chars = "￥%&@"
    
    # 确保密码包含足够的特殊字符（2-4个）
    num_special = random.randint(2, 4)
    num_upper = random.randint(2, 3)
    num_lower = random.randint(2, 3)
    num_digit = random.randint(2, 3)
    
    # 剩余长度随机分配
    remaining = length - (num_special + num_upper + num_lower + num_digit)
    
    # 生成各类字符
    password_chars = []
    password_chars.extend(random.choices(special_chars, k=num_special))
    password_chars.extend(random.choices(string.ascii_uppercase, k=num_upper))
    password_chars.extend(random.choices(string.ascii_lowercase, k=num_lower))
    password_chars.extend(random.choices(string.digits, k=num_digit))
    
    # 填充剩余长度
    if remaining > 0:
        all_chars = string.ascii_letters + string.digits + special_chars
        password_chars.extend(random.choices(all_chars, k=remaining))
    
    # 打乱顺序
    random.shuffle(password_chars)
    
    return ''.join(password_chars)


def signup_account(config, email, referral_code, proxies=None, cookies=None):
    """注册账号，带重试机制和浏览器验证支持"""
    api_base = config.get('api_base', 'https://megallm.io')
    signup_url = f"{api_base}/api/auth/signup"
    max_retries = config.get('retry', {}).get('max_retries', 5)
    retry_delay = config.get('retry', {}).get('retry_delay', 30)

    name = generate_name()
    password = config.get('account', {}).get('password', 'aA1472580369Z@')

    payload = {
        "name": name,
        "email": email,
        "password": password,
        "referralCode": referral_code
    }

    print(f"\n注册信息:")
    print(f"  姓名: {name}")
    print(f"  邮箱: {email}")
    print(f"  密码: {password}")
    print(f"  邀请码: {referral_code}")

    # 设置请求头
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }

    # 使用 session 来管理 cookies
    session = requests.Session()
    if cookies:
        session.cookies.update(cookies)

    retry_count = 0
    while retry_count < max_retries:
        try:
            print(f"\n发起注册请求... (尝试 {retry_count + 1}/{max_retries})")
            response = session.post(signup_url, json=payload, proxies=proxies, headers=headers, timeout=30)

            print(f"响应状态码: {response.status_code}")

            # 检查是否遇到安全验证
            if 'checkpoint' in response.url.lower() or 'verifying your browser' in response.text.lower():
                print("⚠️  检测到 Vercel 安全检查点，需要浏览器验证")
                return {
                    "email": email,
                    "password": password,
                    "name": name,
                    "success": False,
                    "need_browser_verification": True
                }

            # 检查状态码是否为200
            if response.status_code == 200:
                data = response.json()
                print(f"响应内容: {data}")
                
                # 检查message字段
                if data.get('message') == "Verification code sent! Please check your email and verify within 10 minutes.":
                    print("\n✓ 注册成功! 验证码已发送到邮箱")
                    return {
                        "email": email,
                        "password": password,
                        "name": name,
                        "success": True
                    }
                else:
                    print(f"\n✗ 响应message不匹配: {data.get('message')}")
            else:
                print(f"\n✗ 状态码非200: {response.status_code}")
                print(f"响应内容: {response.text}")
            
            # 如果不是最后一次重试，等待后重试
            retry_count += 1
            if retry_count < max_retries:
                print(f"\n等待{retry_delay}秒后重试...")
                time.sleep(retry_delay)

        except requests.RequestException as e:
            print(f"\n请求异常: {e}")
            retry_count += 1
            if retry_count < max_retries:
                print(f"\n等待{retry_delay}秒后重试...")
                time.sleep(retry_delay)
        except json.JSONDecodeError as e:
            print(f"\nJSON解析失败: {e}")
            retry_count += 1
            if retry_count < max_retries:
                print(f"\n等待{retry_delay}秒后重试...")
                time.sleep(retry_delay)
    
    print(f"\n✗ 注册失败: 已达到最大重试次数 ({max_retries})")
    return {
        "email": email,
        "password": password,
        "name": name,
        "success": False
    }


def poll_emails(config, email, proxies=None):
    """轮询邮箱获取邮件列表"""
    email_base = config['email_base']
    timeout = config.get('email_polling', {}).get('timeout', 600)
    poll_interval = config.get('email_polling', {}).get('interval', 5)
    # URL编码邮箱地址，将@替换为%40
    encoded_email = urllib.parse.quote(email)
    api_url = f"{email_base}/api/emails?email={encoded_email}"

    headers = {
        'Referer': f"{email_base}/"
    }

    print(f"\n开始轮询邮箱，超时时间: {timeout}秒")
    print(f"轮询间隔: {poll_interval}秒")

    start_time = time.time()
    attempt = 0

    while time.time() - start_time < timeout:
        attempt += 1
        try:
            print(f"\n[尝试 {attempt}] 检查邮件...")
            response = requests.get(api_url, headers=headers, proxies=proxies)
            response.raise_for_status()
            data = response.json()
            
            # 检查count字段是否为1，表示收到邮件
            if data.get('count') == 1:
                print(f"✓ 收到邮件 (count={data.get('count')})")
                return data.get('emails', [])
            else:
                print(f"暂无邮件 (count={data.get('count', 0)})，等待 {poll_interval} 秒后重试...")
                time.sleep(poll_interval)
                
        except requests.RequestException as e:
            print(f"请求失败: {e}")
            time.sleep(poll_interval)
        except json.JSONDecodeError as e:
            print(f"JSON解析失败: {e}")
            time.sleep(poll_interval)
    
    print(f"\n✗ 轮询超时，未收到邮件")
    return None


def extract_verification_code(emails):
    """从邮件列表中提取验证码"""
    if not emails:
        return None
    
    import re
    
    # 通常验证码在最新的邮件中
    for email in emails:
        print(f"\n检查邮件:")
        print(f"  发件人: {email.get('from_address', 'N/A')}")
        print(f"  主题: {email.get('subject', 'N/A')}")
        print(f"  时间: {email.get('created_at', 'N/A')}")
        
        # 从content字段获取邮件内容
        content = email.get('content', '')
        
        if content:
            # 尝试提取验证码（通常是6位数字）
            # 根据示例，验证码格式为独立的6位数字
            patterns = [
                r'Your Verification Code\s+(\d{6})',  # 匹配"Your Verification Code"后的6位数字
                r'验证码[：:]\s*(\d{4,8})',  # 中文格式
                r'verification code[：:]\s*(\d{4,8})',  # 英文格式
                r'\b(\d{6})\b',  # 6位数字
            ]
            
            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    code = match.group(1)
                    print(f"\n✓ 找到验证码: {code}")
                    return code
    
    print(f"\n✗ 未能从邮件中提取验证码")
    return None


def verify_email(config, email, otp, proxies=None, cookies=None):
    """验证邮箱"""
    api_base = config.get('api_base', 'https://megallm.io')
    verify_url = f"{api_base}/api/auth/verify"

    payload = {
        "email": email,
        "otp": otp
    }

    print(f"\n发起验证请求...")
    print(f"  邮箱: {email}")
    print(f"  验证码: {otp}")

    # 调试: 显示 cookies 信息
    if cookies:
        print(f"  使用 Cookies: {len(cookies)} 个")
        important_cookies = ['_vercel_jwt', '__vercel_live_token', 'vercel-checkpoint']
        for key in important_cookies:
            if key in cookies:
                print(f"    - {key}: 已设置")
    else:
        print(f"  ⚠️  未使用 Cookies")

    # 设置请求头
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }

    # 使用 session 来管理 cookies (与 signup_account 保持一致)
    session = requests.Session()
    if cookies:
        session.cookies.update(cookies)

    try:
        response = session.post(verify_url, json=payload, proxies=proxies, headers=headers, timeout=30)
        
        print(f"\n响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"响应内容: {data}")

            # 检查verified字段
            if data.get('verified') == True:
                print("\n✓ 邮箱验证成功!")
                return {
                    "success": True,
                    "userId": data.get('userId'),
                    "apiKey": data.get('apiKey'),
                    "message": data.get('message')
                }
            else:
                print(f"\n✗ 验证失败: verified={data.get('verified')}")
                return {"success": False}
        else:
            # 检查是否遇到安全验证
            if 'checkpoint' in response.url.lower() or 'verifying your browser' in response.text.lower():
                print("⚠️  检测到 Vercel 安全检查点，需要浏览器验证")
                return {"success": False, "need_browser_verification": True}

            print(f"\n✗ 验证失败: 状态码{response.status_code}")
            print(f"响应内容: {response.text}")
            return {"success": False}
            
    except requests.RequestException as e:
        print(f"\n验证请求异常: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"错误详情: {e.response.text}")
        return {"success": False}
    except json.JSONDecodeError as e:
        print(f"\nJSON解析失败: {e}")
        return {"success": False}


def login_and_get_session(config, email, password, proxies=None):
    """登录并获取session token"""
    api_base = config.get('api_base', 'https://megallm.io')

    try:
        # 使用session来保持cookie
        session = requests.Session()

        # 步骤0: 访问session接口获取初始cookies
        print(f"\n访问session接口...")
        session_response = session.get(f"{api_base}/api/auth/session", proxies=proxies)
        print(f"✓ Session接口响应: {session_response.status_code}")

        # 步骤1: 获取CSRF token
        print(f"\n获取CSRF token...")
        csrf_response = session.get(f"{api_base}/api/auth/csrf", proxies=proxies)
        csrf_data = csrf_response.json()
        csrf_token = csrf_data.get('csrfToken')

        if not csrf_token:
            print("✗ 未能获取CSRF token")
            return None

        print(f"✓ CSRF token: {csrf_token[:20]}...")

        # 步骤2: 登录获取session token
        print(f"\n发起登录请求...")
        login_data = {
            'email': email,
            'password': password,
            'redirect': 'false',
            'csrfToken': csrf_token,
            'callbackUrl': f'{api_base}/auth/signin',
            'json': 'true'
        }

        # 使用session发送请求，自动携带所有cookies
        login_response = session.post(
            f"{api_base}/api/auth/callback/credentials",
            data=login_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            proxies=proxies
        )
        
        print(f"登录响应状态码: {login_response.status_code}")
        print(f"登录响应内容: {login_response.text[:200]}...")
        
        # 从cookies中提取session token
        session_token = session.cookies.get('__Secure-next-auth.session-token')
        
        if session_token:
            print(f"✓ 成功获取session token")
            return session_token
        else:
            print(f"✗ 未能获取session token")
            print(f"所有cookies: {list(session.cookies.keys())}")
            return None
            
    except Exception as e:
        print(f"✗ 登录异常: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_referral_stats(config, session_token, proxies=None):
    """获取推荐统计信息"""
    api_base = config.get('api_base', 'https://megallm.io')

    try:
        print(f"\n获取推荐统计...")
        cookies = {'__Secure-next-auth.session-token': session_token}

        response = requests.get(
            f"{api_base}/api/referral/stats",
            cookies=cookies,
            proxies=proxies
        )
        
        if response.status_code == 200:
            data = response.json()
            referral_code = data.get('referralCode')
            total_referred = data.get('stats', {}).get('totalReferred', 0)
            
            print(f"✓ 推荐码: {referral_code}")
            print(f"  总推荐人数: {total_referred}")
            
            return referral_code
        else:
            print(f"✗ 获取推荐统计失败: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"✗ 获取推荐统计异常: {e}")
        return None


def save_to_csv(email, password, api_key, csv_file='accounts.csv'):
    """保存账号信息到CSV文件"""
    import csv
    import os
    
    # 检查文件是否存在，如果不存在则创建并写入表头
    file_exists = os.path.isfile(csv_file)
    
    with open(csv_file, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        
        # 如果文件不存在，先写入表头
        if not file_exists:
            writer.writerow(['Email', 'Password', 'API Key', 'Created At'])
        
        # 写入账号信息
        from datetime import datetime
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        writer.writerow([email, password, api_key, created_at])
    
    print(f"\n✓ 账号信息已保存到 {csv_file}")


# 全局邀请码池
REFERRAL_CODE_POOL = []
REFERRAL_POOL_FILE = 'referral_pool.json'
REFERRAL_POOL_ENABLED = False  # 邀请码池是否启用


def load_referral_pool(config):
    """从配置和本地文件加载邀请码池"""
    global REFERRAL_CODE_POOL, REFERRAL_POOL_ENABLED

    # 检查是否启用邀请码池功能
    referral_pool_config = config.get('referral_pool', {})
    REFERRAL_POOL_ENABLED = referral_pool_config.get('enabled', False)

    if not REFERRAL_POOL_ENABLED:
        print("⚠ 邀请码池功能已禁用，将使用配置中的固定邀请码")
        return

    print("✓ 邀请码池功能已启用")

    # 加载初始邀请码
    initial_codes = referral_pool_config.get('initial_codes', [])
    REFERRAL_CODE_POOL = initial_codes.copy()

    # 从本地文件加载已保存的邀请码
    if os.path.exists(REFERRAL_POOL_FILE):
        try:
            with open(REFERRAL_POOL_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    # 文件为空，使用初始邀请码
                    saved_codes = []
                    print(f"⚠ 本地邀请码池文件为空，使用初始邀请码")
                else:
                    saved_codes = json.loads(content)
                    print(f"✓ 已从本地加载邀请码")

                # 合并初始码和已保存的码，去重
                REFERRAL_CODE_POOL = list(set(REFERRAL_CODE_POOL + saved_codes))
            print(f"✓ 当前邀请码池包含 {len(REFERRAL_CODE_POOL)} 个邀请码")
        except json.JSONDecodeError as e:
            print(f"⚠ 邀请码池文件格式错误: {e}，使用初始邀请码")
            # 保持使用初始邀请码
        except Exception as e:
            print(f"⚠ 加载本地邀请码池失败: {e}，使用初始邀请码")
            # 保持使用初始邀请码
    else:
        print(f"本地无邀请码池文件，使用初始邀请码: {len(REFERRAL_CODE_POOL)} 个")


def save_referral_pool():
    """将邀请码池保存到本地文件"""
    global REFERRAL_CODE_POOL, REFERRAL_POOL_ENABLED

    if not REFERRAL_POOL_ENABLED:
        return

    try:
        with open(REFERRAL_POOL_FILE, 'w', encoding='utf-8') as f:
            json.dump(REFERRAL_CODE_POOL, f, ensure_ascii=False, indent=2)
        print(f"✓ 邀请码池已保存到本地文件")
    except Exception as e:
        print(f"⚠ 保存邀请码池失败: {e}")


def update_referral_pool(new_code):
    """更新邀请码池并保存到本地"""
    global REFERRAL_CODE_POOL, REFERRAL_POOL_ENABLED

    if not REFERRAL_POOL_ENABLED:
        return

    if new_code and new_code not in REFERRAL_CODE_POOL:
        REFERRAL_CODE_POOL.append(new_code)
        print(f"\n✓ 邀请码池已更新，当前包含 {len(REFERRAL_CODE_POOL)} 个邀请码")
        save_referral_pool()


def get_random_referral_code(config):
    """从池中随机获取邀请码，如果池功能未启用或池为空则使用配置中的"""
    global REFERRAL_CODE_POOL, REFERRAL_POOL_ENABLED

    # 优先使用邀请码池（如果启用且非空）
    if REFERRAL_POOL_ENABLED and REFERRAL_CODE_POOL:
        code = random.choice(REFERRAL_CODE_POOL)
        print(f"使用邀请码池中的邀请码: {code}")
        return code

    # 邀请码池为空或未启用，fallback到配置中的固定邀请码
    code = config.get('referral_code', '')
    if code:
        print(f"使用配置中的固定邀请码: {code}")
        return code

    # 两者都没有配置
    print(f"⚠️  警告: 未配置邀请码!")
    print(f"   请在 config.json 中设置 'referral_code' 或 'referral_pool.initial_codes'")
    print(f"   否则注册可能会失败! 继续尝试...")
    return ''


def register_once(config, proxy_pool=None, task_id=None, cookie_manager=None):
    """执行一次完整的注册流程，支持浏览器验证"""
    print("\n" + "="*60)
    print("开始新的注册流程")
    print("="*60)

    # 添加任务启动时的随机延迟，避免所有任务同时开始
    if task_id is not None:
        import time
        import random
        startup_delay = random.uniform(0.5, 2)
        time.sleep(startup_delay)

    # 获取代理
    proxies = None
    current_proxy = None
    if proxy_pool:
        current_proxy = proxy_pool.get_next_proxy()
        if current_proxy:
            proxies = proxy_pool.get_proxies_dict()
            # 功能1: 显示当前使用的节点
            if task_id is not None:
                print(f"[任务 {task_id}] 当前使用节点: {current_proxy}")
            else:
                print(f"使用代理节点: {current_proxy}")
        else:
            print("⚠ 无可用代理，将不使用代理")

    # 获取或生成 cookies
    cookies = None
    browser_session = None
    if cookie_manager and config.get('browser', {}).get('enabled', True):
        # 检查是否有缓存的有效 cookies
        if not cookie_manager.is_expired(current_proxy, max_age=1800):  # 30分钟有效期
            cached_data = cookie_manager.get_cookies(current_proxy)
            if cached_data:
                cookies = cached_data.get('cookies', {})
                if cookies:
                    print(f"🍪 使用缓存的 cookies (代理: {current_proxy or 'default'})")

        # 如果没有有效 cookies，则通过浏览器获取
        if not cookies:
            print(f"🌐 通过浏览器获取新的验证 cookies...")
            browser_session = BrowserSession(config)
            cookies = browser_session.get_verified_session(current_proxy)
            if cookies:
                cookie_manager.set_cookies(cookies, current_proxy)
                print(f"✅ 已保存 cookies 到缓存")
            else:
                print(f"⚠️  未能获取 cookies，将尝试直接请求")

    # 步骤1: 获取邀请码
    print("\n[步骤1] 获取邀请码...")
    referral_code = get_random_referral_code(config)

    # 步骤2: 获取临时邮箱
    print("\n[步骤2] 获取临时邮箱...")
    email = get_temp_email(config, proxies=proxies)

    if not email:
        print("✗ 获取邮箱失败")
        if proxy_pool and current_proxy:
            proxy_pool.mark_proxy_failed(current_proxy)
        return False

    # 步骤3: 注册账号
    print("\n[步骤3] 注册账号...")

    # 添加随机延迟，避免并发请求被识别为攻击
    import time
    import random
    delay = random.uniform(5, 10)  # 增加到5-10秒
    print(f"⏱️  等待 {delay:.1f} 秒后发起注册请求...")
    time.sleep(delay)

    account_info = signup_account(config, email, referral_code, proxies=proxies, cookies=cookies)

    # 检查是否需要浏览器验证
    if not account_info['success']:
        if account_info.get('need_browser_verification') and cookie_manager:
            print("\n❌ 遭遇 Vercel 安全拦截 (429 Too Many Requests)")
            print("📊 原因分析:")
            print("   1. 当前代理 IP 可能已被 Vercel 标记为可疑")
            print("   2. 即使重新获取 cookies，同一 IP 短时间内重试仍会被拦截")
            print("   3. 需要更长的冷却时间或切换到全新的 IP")
            print("\n🔄 策略：放弃当前代理，等待下一批次使用新代理重试")
            print(f"   当前代理 [{current_proxy}] 将增加失败计数")

            # 标记当前代理失败
            if proxy_pool and current_proxy:
                proxy_pool.mark_proxy_failed(current_proxy)

            # 直接返回失败，不在同一代理上重试
            return False

        if not account_info['success']:
            print("\n✗ 注册失败")
            if proxy_pool and current_proxy:
                proxy_pool.mark_proxy_failed(current_proxy)
            return False

    # 步骤4: 轮询邮箱获取验证码
    print("\n[步骤4] 轮询邮箱获取验证码...")
    emails = poll_emails(config, email, proxies=proxies)

    if not emails:
        print("\n✗ 未收到验证邮件")
        if proxy_pool and current_proxy:
            proxy_pool.mark_proxy_failed(current_proxy)
        return False

    # 步骤5: 提取验证码
    print("\n[步骤5] 提取验证码...")
    verification_code = extract_verification_code(emails)

    if not verification_code:
        print("\n✗ 未能提取验证码")
        return False

    # 步骤6: 验证邮箱
    print("\n[步骤6] 验证邮箱...")

    # 添加随机延迟，避免并发请求被识别为攻击
    import time
    import random
    delay = random.uniform(1, 3)
    print(f"⏱️  等待 {delay:.1f} 秒后发起验证请求...")
    time.sleep(delay)

    verify_result = verify_email(config, email, verification_code, proxies=proxies, cookies=cookies)

    if not verify_result['success']:
        # 检查是否需要浏览器验证(验证步骤)
        if verify_result.get('need_browser_verification') and cookie_manager:
            print("\n🔄 验证步骤检测到需要浏览器验证，重新获取 cookies 后重试...")

            # 添加随机延迟，避免立即重试被识别
            delay = random.uniform(3, 6)
            print(f"⏱️  等待 {delay:.1f} 秒后重新获取 cookies...")
            time.sleep(delay)

            # 清除旧的 cookies
            if current_proxy:
                cookie_manager.clear_cookies(current_proxy)
            else:
                cookie_manager.clear_cookies()

            # 重新获取浏览器 session
            browser_session = BrowserSession(config)
            cookies = browser_session.get_verified_session(current_proxy)

            if cookies:
                # 保存新的 cookies
                if current_proxy:
                    cookie_manager.set_cookies(cookies, current_proxy)
                else:
                    cookie_manager.set_cookies(cookies)

                # 再次等待，避免连续请求
                delay2 = random.uniform(2, 4)
                print(f"⏱️  等待 {delay2:.1f} 秒后重试验证...")
                time.sleep(delay2)

                # 重试验证邮箱
                print("🔄 使用新 cookies 重试验证...")
                verify_result = verify_email(config, email, verification_code, proxies=proxies, cookies=cookies)

        if not verify_result['success']:
            print("\n✗ 邮箱验证失败")
            if proxy_pool and current_proxy:
                proxy_pool.mark_proxy_failed(current_proxy)
            return False

    # 步骤7: 保存账号信息
    print("\n[步骤7] 保存账号信息...")
    save_to_csv(email, account_info['password'], verify_result['apiKey'])

    # 步骤8: 登录获取session token并更新邀请码池
    print("\n[步骤8] 登录获取推荐码...")
    session_token = login_and_get_session(config, email, account_info['password'], proxies=proxies)

    if session_token:
        new_referral_code = get_referral_stats(config, session_token, proxies=proxies)
        if new_referral_code:
            update_referral_pool(new_referral_code)
    else:
        print("⚠ 未能获取session token，跳过邀请码池更新")

    print("\n" + "="*60)
    print("✓ 注册流程成功完成!")
    print(f"  邮箱: {email}")
    print(f"  密码: {account_info['password']}")
    print(f"  API Key: {verify_result['apiKey']}")
    print("="*60)

    return True


def main():
    print("="*60)
    print("自动注册机启动")
    print("="*60)

    # 加载配置
    config = load_config()

    # 加载本地邀请码池
    print("\n加载邀请码池配置...")
    load_referral_pool(config)

    # 初始化代理池
    print("\n初始化代理池...")
    proxy_pool = ProxyPool(config)

    # 初始化 Cookie 管理器
    print("\n初始化 Cookie 管理器...")
    cookie_manager = CookieManager('browser_cookies.json')
    browser_enabled = config.get('browser', {}).get('enabled', True)
    if browser_enabled:
        print("✅ 浏览器验证已启用")
    else:
        print("⚠️  浏览器验证已禁用")
        cookie_manager = None

    # 启动前健康检查
    print("\n执行启动前代理健康检查...")
    proxy_pool.health_check_all()

    if not proxy_pool.active_proxies:
        print("\n✗ 没有可用的代理节点，程序退出")
        return

    # 获取并发任务数
    concurrent_tasks = config.get('proxy_pool', {}).get('concurrent_tasks', 5)
    health_check_interval = config.get('proxy_pool', {}).get('health_check_interval', 300)

    print(f"\n并发配置:")
    print(f"  并发任务数: {concurrent_tasks}")
    print(f"  健康检查间隔: {health_check_interval}秒")

    # 统计信息
    success_count = 0
    fail_count = 0
    last_health_check = time.time()

    # 定义单个并发任务
    def concurrent_register_task(task_id):
        """单个并发注册任务"""
        print(f"\n[任务 {task_id}] 开始执行...")
        try:
            result = register_once(config, proxy_pool=proxy_pool, task_id=task_id, cookie_manager=cookie_manager)
            return (task_id, result)
        except Exception as e:
            print(f"\n[任务 {task_id}] 异常: {e}")
            import traceback
            traceback.print_exc()
            return (task_id, False)

    # 并发执行循环
    task_counter = 0

    try:
        while True:
            # 检查是否需要进行健康检查
            if time.time() - last_health_check > health_check_interval:
                print("\n" + "="*60)
                print("执行定时健康检查...")
                print("="*60)
                proxy_pool.health_check_all()
                last_health_check = time.time()

            # 使用线程池并发执行注册任务
            print("\n" + "="*60)
            print(f"启动并发批次 - 并发数: {concurrent_tasks}")
            print("="*60)

            with ThreadPoolExecutor(max_workers=concurrent_tasks) as executor:
                # 提交任务，添加随机延迟避免同时发起请求
                futures = []
                for i in range(concurrent_tasks):
                    task_counter += 1
                    future = executor.submit(concurrent_register_task, task_counter)
                    futures.append(future)

                    # 在任务之间添加随机延迟(除了最后一个任务)
                    if i < concurrent_tasks - 1:
                        delay = random.uniform(8, 15)  # 增加到8-15秒
                        print(f"⏱️  任务间隔延迟 {delay:.1f} 秒...")
                        time.sleep(delay)

                # 等待所有任务完成
                for future in as_completed(futures):
                    task_id, result = future.result()

                    if result:
                        success_count += 1
                        print(f"\n[任务 {task_id}] ✓ 成功")
                    else:
                        fail_count += 1
                        print(f"\n[任务 {task_id}] ✗ 失败")

                    print(f"当前统计: 成功 {success_count} 次, 失败 {fail_count} 次")

            # 并发批次完成，等待一段时间再开始下一批次
            print("\n等待30秒后进行下一批次...")
            time.sleep(30)

    except KeyboardInterrupt:
        print("\n\n用户中断，程序退出")
        print(f"\n最终统计: 成功 {success_count} 次, 失败 {fail_count} 次")
        print(f"代理池状态: 可用节点 {len(proxy_pool.active_proxies)}/{len(proxy_pool.all_proxies)}")
    except Exception as e:
        print(f"\n✗ 主程序异常: {e}")
        import traceback
        traceback.print_exc()
        print(f"\n最终统计: 成功 {success_count} 次, 失败 {fail_count} 次")


if __name__ == "__main__":
    main()
