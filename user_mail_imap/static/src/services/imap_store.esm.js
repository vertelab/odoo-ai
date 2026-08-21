/** @odoo-module **/
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";

const store = {
    dependencies: [],
    start(env) {
        let state = {
            folders: [],
            messages: [],
            selectedMessage: null,
            currentFolder: 'INBOX',
            hasPassword: false,
            totalMessages: 0,
            offset: 0,
            loading: false,
            loadingDetail: false,
        };

        // Cache: store data per folder
        const folderCache = {};       // folderCache[folder] = { messages, total, offset }
        let foldersCached = null;
        let selectedUid = null;       // Track requested UID to prevent race conditions
        let lastFolderRequested = null;

        const listeners = new Set();

        function notify() {
            listeners.forEach(fn => fn(state));
        }

        function syncState(src) {
            Object.assign(state, src);
            notify();
        }

        return {
            subscribe(fn) {
                listeners.add(fn);
                return () => listeners.delete(fn);
            },
            getState() {
                return state;
            },

            async checkPassword() {
                const res = await rpc('/imap/check');
                syncState({ hasPassword: res.has_password });
                return res.has_password;
            },

            async setPassword(password) {
                await rpc('/imap/set_password', { password });
                syncState({ hasPassword: true });
            },

            async loadFolders(force = false) {
                // Serve cached immediately, refresh in background
                if (foldersCached && !force) {
                    syncState({ folders: foldersCached });
                    // Background refresh
                    this._refreshFolders();
                    return foldersCached;
                }
                const folders = await rpc('/imap/folders');
                foldersCached = folders;
                syncState({ folders });
                return folders;
            },

            async _refreshFolders() {
                try {
                    const folders = await rpc('/imap/folders');
                    if (JSON.stringify(folders) !== JSON.stringify(foldersCached)) {
                        foldersCached = folders;
                        syncState({ folders });
                    }
                } catch (e) { /* silent */ }
            },

            async loadMessages(folder, offset = 0) {
                // Always show loading when switching folders
                const switchingFolder = state.currentFolder !== folder;
                if (switchingFolder) {
                    syncState({ loading: true, currentFolder: folder, selectedMessage: null });
                }

                const cacheKey = folder;
                const cached = folderCache[cacheKey];

                if (offset === 0 && cached) {
                    // Return cached immediately, then background refresh
                    syncState({
                        messages: cached.messages,
                        totalMessages: cached.total,
                        offset: cached.offset,
                        currentFolder: folder,
                        loading: false,
                    });
                    this._refreshMessages(folder);
                    // Preload other folders in background
                    this._preloadFolders(folder);
                    return;
                }

                if (offset > 0) {
                    syncState({ loading: true });
                }

                try {
                    const res = await rpc('/imap/mails', { folder, offset, limit: 80 });
                    const messages = offset === 0
                        ? res.messages
                        : [...(cached?.messages || []), ...res.messages];

                    folderCache[cacheKey] = { messages, total: res.total, offset: offset + 80 };
                    syncState({
                        messages,
                        totalMessages: res.total,
                        offset: offset + 80,
                        currentFolder: folder,
                        loading: false,
                    });
                    // Preload other folders in background
                    this._preloadFolders(folder);
                } catch (e) {
                    syncState({ loading: false });
                }
            },

            async _preloadFolders(excludeFolder) {
                // Preload 25 messages from other folders in background
                if (!foldersCached) return;
                const toPreload = foldersCached
                    .filter(f => f.name !== excludeFolder && !folderCache[f.name])
                    .slice(0, 3); // preload max 3 other folders
                for (const f of toPreload) {
                    try {
                        const res = await rpc('/imap/mails', { folder: f.name, offset: 0, limit: 25 });
                        folderCache[f.name] = { messages: res.messages, total: res.total, offset: 25 };
                    } catch (e) { /* silent */ }
                }
            },

            async _refreshMessages(folder) {
                try {
                    const res = await rpc('/imap/mails', { folder, offset: 0, limit: 80 });
                    const cached = folderCache[folder];
                    if (!cached || JSON.stringify(res.messages) !== JSON.stringify(cached.messages)) {
                        folderCache[folder] = { messages: res.messages, total: res.total, offset: 50 };
                        if (state.currentFolder === folder) {
                            syncState({
                                messages: res.messages,
                                totalMessages: res.total,
                                offset: 50,
                            });
                        }
                    }
                } catch (e) { /* silent */ }
            },

            async loadMail(folder, uid) {
                // Track the requested UID to prevent out-of-order responses
                const requestUid = uid;
                selectedUid = requestUid;
                syncState({ loadingDetail: true });

                try {
                    const msg = await rpc('/imap/mail', { folder, uid });
                    // Only apply if this is still the latest request
                    if (selectedUid === requestUid) {
                        syncState({ selectedMessage: msg, loadingDetail: false });
                    }
                } catch (e) {
                    if (selectedUid === requestUid) {
                        syncState({ loadingDetail: false });
                    }
                }
            },

            async sendMail(to, subject, body, cc) {
                await rpc('/imap/send', { to, subject, body, cc });
            },

            async setFlag(folder, uids, flag, value) {
                await rpc('/imap/flag', { folder, uids, flag, value });
                // Invalidate cache
                delete folderCache[folder];
            },

            async fetchTemplates() {
                const res = await rpc('/imap/templates');
                return res.templates || [];
            },

            async renderTemplate(templateId) {
                return await rpc('/imap/template/render', { template_id: templateId });
            },

            async searchPartners(query) {
                const res = await rpc('/imap/partners', { query });
                return res.partners || [];
            },

            async sync() {
                await rpc('/imap/sync');
                this.invalidateCache();
            },

            invalidateCache() {
                for (const key of Object.keys(folderCache)) {
                    delete folderCache[key];
                }
                foldersCached = null;
            },

            invalidateFolderCache() {
                for (const key of Object.keys(folderCache)) {
                    delete folderCache[key];
                }
                foldersCached = null;
            },
        };
    },
};

registry.category("services").add("user_mail_imap.store", store);
