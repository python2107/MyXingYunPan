// 星云盘 - 好友对话页 JS
// 加载好友列表、选择好友、收发消息
(function () {
    let currentFriendId = null;
    let currentFriendName = '';
    let friends = [];
    let pollTimer = null;

    const friendListEl = document.getElementById('friendList');
    const chatHeaderEl = document.getElementById('chatHeader');
    const chatHeaderStatusEl = document.querySelector('.chat-header-status');
    const chatAvatarEl = document.getElementById('chatAvatar');
    const chatMessagesEl = document.getElementById('chatMessages');
    const messageInputEl = document.getElementById('messageInput');
    const sendBtnEl = document.getElementById('sendBtn');

    // ---------- 工具函数 ----------
    function escapeHtml(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatTime(ts) {
        if (!ts) return '';
        // 输入格式：YYYY-MM-DD HH:MM:SS
        const m = String(ts).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
        if (!m) return String(ts);
        return m[1] + '-' + m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5];
    }

    function showEmpty(text) {
        chatMessagesEl.innerHTML = '<div class="msg-empty">' + escapeHtml(text) + '</div>';
    }

    // ---------- 好友列表 ----------
    async function loadFriends() {
        try {
            const res = await fetch('/api/friends');
            const data = await res.json();
            if (!data.success) {
                friendListEl.innerHTML = '<div class="msg-empty" style="padding:20px;">加载失败</div>';
                return;
            }
            friends = data.friends || [];
            renderFriendList();
        } catch (e) {
            friendListEl.innerHTML = '<div class="msg-empty" style="padding:20px;">加载失败</div>';
        }
    }

    function renderFriendList() {
        if (!friends.length) {
            friendListEl.innerHTML = '<div class="msg-empty" style="padding:20px;">暂无好友，先去添加好友吧</div>';
            return;
        }
        let html = '';
        friends.forEach(function (f) {
            const active = currentFriendId === f.id ? ' active' : '';
            html +=
                '<div class="friend-item' + active + '" data-id="' + f.id + '" data-name="' + escapeHtml(f.username) + '">' +
                '<div class="friend-avatar">' + escapeHtml(String(f.username).charAt(0).toUpperCase()) + '</div>' +
                '<div class="friend-info">' +
                '<div class="friend-name">' + escapeHtml(f.username) + '</div>' +
                '<div class="friend-status">在线</div>' +
                '</div>' +
                '</div>';
        });
        friendListEl.innerHTML = html;
        friendListEl.querySelectorAll('.friend-item').forEach(function (item) {
            item.addEventListener('click', function () {
                selectFriend(parseInt(item.getAttribute('data-id'), 10), item.getAttribute('data-name'));
            });
        });
    }

    // ---------- 选择好友 ----------
    function selectFriend(id, name) {
        currentFriendId = id;
        currentFriendName = name;
        chatHeaderEl.textContent = name;
        chatHeaderStatusEl.textContent = '与 ' + name + ' 的对话';
        chatAvatarEl.textContent = String(name).charAt(0).toUpperCase();
        messageInputEl.disabled = false;
        sendBtnEl.disabled = false;
        document.getElementById('fileBtn').disabled = false;
        // 高亮选中项
        friendListEl.querySelectorAll('.friend-item').forEach(function (item) {
            item.classList.toggle('active', parseInt(item.getAttribute('data-id'), 10) === id);
        });
        loadMessages();
        startPolling();
    }

    // ---------- 消息加载 ----------
    async function loadMessages() {
        if (!currentFriendId) return;
        try {
            const res = await fetch('/api/messages?friend_id=' + currentFriendId);
            const data = await res.json();
            if (!data.success) {
                showEmpty('加载失败');
                return;
            }
            renderMessages(data.messages || []);
        } catch (e) {
            showEmpty('加载失败');
        }
    }

    function renderMessages(messages) {
        if (!messages.length) {
            showEmpty('还没有消息，发一条吧');
            return;
        }
        let html = '';
        messages.forEach(function (m) {
            const isSelf = m.sender_id === currentFriendId ? false : true;
            const cls = isSelf ? 'msg-self' : 'msg-other';
            let content = escapeHtml(m.content || '');
            if (m.file) {
                content += '<div class="msg-file"><a href="' + m.file.download_url + '">' +
                    escapeHtml(m.file.name) + ' (' + escapeHtml(m.file.size_human) + ')</a></div>';
            }
            if (m.image_url) {
                content += '<img src="' + escapeHtml(m.image_url) + '" style="max-width:200px;border-radius:8px;display:block;margin-top:6px;">';
            }
            html += '<div class="msg-bubble ' + cls + '">' + content +
                '<div class="msg-time">' + formatTime(m.time) + '</div></div>';
        });
        chatMessagesEl.innerHTML = html;
        chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
    }

    // ---------- 发送消息 ----------
    async function sendMessage() {
        const content = messageInputEl.value.trim();
        if (!content || !currentFriendId) return;
        try {
            const res = await fetch('/api/messages/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ receiver_id: currentFriendId, content: content })
            });
            const data = await res.json();
            if (data.success) {
                messageInputEl.value = '';
                loadMessages();
            } else {
                alert(data.error || '发送失败');
            }
        } catch (e) {
            alert('发送失败');
        }
    }

    // ---------- 发送云盘文件 ----------
    const fileModalEl = document.getElementById('fileModal');
    const mineFileListEl = document.getElementById('mineFileList');
    const publicFileListEl = document.getElementById('publicFileList');

    function openFileModal() {
        if (!currentFriendId) return;
        mineFileListEl.innerHTML = '<div class="msg-empty" style="padding:16px;">加载中...</div>';
        publicFileListEl.innerHTML = '';
        fileModalEl.style.display = 'flex';
        loadAvailableFiles();
    }

    async function loadAvailableFiles() {
        try {
            const res = await fetch('/api/chat/available_files');
            const data = await res.json();
            if (!data.success) { mineFileListEl.innerHTML = '<div class="msg-empty" style="padding:16px;">加载失败</div>'; return; }
            renderFileChoices(mineFileListEl, data.mine, '我');
            renderFileChoices(publicFileListEl, data.public, null);
            if (!data.public.length) publicFileListEl.innerHTML = '<div class="msg-empty" style="padding:16px;">暂无他人公开文件</div>';
        } catch (e) {
            mineFileListEl.innerHTML = '<div class="msg-empty" style="padding:16px;">加载失败</div>';
        }
    }

    function renderFileChoices(container, files, ownerLabel) {
        if (!files || !files.length) {
            container.innerHTML = '<div class="msg-empty" style="padding:16px;">' + (ownerLabel === null ? '' : '暂无文件') + '</div>';
            return;
        }
        let html = '';
        files.forEach(function (f) {
            html +=
                '<div class="friend-item file-choice" data-id="' + f.id + '">' +
                '<div class="friend-info" style="min-width:0;">' +
                '<div class="friend-name" style="word-break:break-all;">' + escapeHtml(f.name) + ' (' + escapeHtml(f.size_human) + ')</div>' +
                '<div class="friend-status">' + (f.owner ? escapeHtml(f.owner) : '我') + '</div>' +
                '</div>' +
                '</div>';
        });
        container.innerHTML = html;
        container.querySelectorAll('.file-choice').forEach(function (item) {
            item.addEventListener('click', function () {
                sendFileMessage(parseInt(item.getAttribute('data-id'), 10));
            });
        });
    }

    async function sendFileMessage(fileId) {
        try {
            const res = await fetch('/api/messages/send_file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ receiver_id: currentFriendId, file_id: fileId, type: 'file', content: '' })
            });
            const data = await res.json();
            if (data.success) {
                fileModalEl.style.display = 'none';
                loadMessages();
                alert('文件已发送');
            } else {
                alert(data.error || '发送失败');
            }
        } catch (e) {
            alert('发送失败');
        }
    }

    // ---------- 轮询新消息 ----------
    function startPolling() {
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(function () {
            if (currentFriendId) loadMessages();
        }, 5000);
    }

    // ---------- 事件绑定 ----------
    const fileBtnEl = document.getElementById('fileBtn');
    sendBtnEl.addEventListener('click', sendMessage);
    fileBtnEl.addEventListener('click', openFileModal);
    document.getElementById('closeFileModalBtn').addEventListener('click', function () {
        fileModalEl.style.display = 'none';
    });
    window.addEventListener('click', function (e) {
        if (e.target === fileModalEl) fileModalEl.style.display = 'none';
    });
    messageInputEl.addEventListener('keypress', function (e) {
        if (e.key === 'Enter') sendMessage();
    });
    document.getElementById('friendSearch').addEventListener('input', function () {
        const q = this.value.trim().toLowerCase();
        friendListEl.querySelectorAll('.friend-item').forEach(function (item) {
            const name = String(item.getAttribute('data-name') || '').toLowerCase();
            item.style.display = name.indexOf(q) >= 0 ? '' : 'none';
        });
    });

    loadFriends();
})();
