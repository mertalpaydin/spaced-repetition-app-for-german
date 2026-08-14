/**
 * Offline IndexedDB Storage Manager for DeutschMaster PWA.
 * Stores topic states, FSRS cards, and offline review events.
 */

const DB_NAME = 'DeutschMasterDB';
const DB_VERSION = 1;

class OfflineStorage {
  constructor() {
    this.db = null;
  }

  async init() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);

      request.onupgradeneeded = (event) => {
        const db = event.target.result;

        if (!db.objectStoreNames.contains('topic_states')) {
          db.createObjectStore('topic_states', { keyPath: 'topic_id' });
        }
        if (!db.objectStoreNames.contains('fsrs_cards')) {
          db.createObjectStore('fsrs_cards', { keyPath: 'card_id' });
        }
        if (!db.objectStoreNames.contains('review_logs')) {
          const store = db.createObjectStore('review_logs', { keyPath: 'id', autoIncrement: true });
          store.createIndex('by_card', 'card_id', { unique: false });
        }
      };

      request.onsuccess = (event) => {
        this.db = event.target.result;
        resolve(this.db);
      };

      request.onerror = (event) => {
        reject(event.target.error);
      };
    });
  }

  async saveTopicState(topicState) {
    return this._put('topic_states', topicState);
  }

  async getTopicStates() {
    return this._getAll('topic_states');
  }

  async saveFSRSCard(card) {
    return this._put('fsrs_cards', card);
  }

  async getFSRSCards() {
    return this._getAll('fsrs_cards');
  }

  async appendReviewLog(logEntry) {
    return this._put('review_logs', logEntry);
  }

  async getReviewLog() {
    return this._getAll('review_logs');
  }

  /**
   * Replace the entire local state (review log + derived topic/FSRS state)
   * atomically-ish (store by store). Used by import: the imported review_log
   * is the only trusted input, derived state is recomputed by the caller and
   * handed in here to persist, never assigned from the imported file as-is.
   */
  async replaceAll({ reviewLog = [], topicStates = {}, fsrsCards = {} } = {}) {
    await this._clear('review_logs');
    await this._clear('topic_states');
    await this._clear('fsrs_cards');
    for (const entry of reviewLog) {
      await this._put('review_logs', entry);
    }
    for (const state of Object.values(topicStates)) {
      await this._put('topic_states', state);
    }
    for (const card of Object.values(fsrsCards)) {
      await this._put('fsrs_cards', card);
    }
  }

  async _clear(storeName) {
    if (!this.db) await this.init();
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction(storeName, 'readwrite');
      const req = tx.objectStore(storeName).clear();
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }

  async _put(storeName, value) {
    if (!this.db) await this.init();
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction(storeName, 'readwrite');
      const store = tx.objectStore(storeName);
      const req = store.put(value);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async _getAll(storeName) {
    if (!this.db) await this.init();
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction(storeName, 'readonly');
      const store = tx.objectStore(storeName);
      const req = store.getAll();
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
}

window.offlineStorage = new OfflineStorage();
