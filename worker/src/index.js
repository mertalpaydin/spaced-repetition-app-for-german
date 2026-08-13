/**
 * Cloudflare Worker Sync Handler for DeutschMaster.
 * Implements REST sync endpoints against Cloudflare D1.
 */

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-User-ID',
};

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json',
      ...CORS_HEADERS,
    },
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: CORS_HEADERS });
    }

    // 1. Health Check
    if (path === '/api/health') {
      return jsonResponse({
        status: 'healthy',
        timestamp: new Date().toISOString(),
        version: env.API_VERSION || '1.0.0',
      });
    }

    // 2. Manifest Endpoint
    if (path === '/api/manifest') {
      return jsonResponse({
        format_version: 1,
        engine: 'FSRS-4.5',
        sync_supported: true,
        timestamp: new Date().toISOString(),
      });
    }

    // 3. Sync Push Endpoint
    if (path === '/api/sync/push' && request.method === 'POST') {
      try {
        const payload = await request.json();
        const userId = payload.user_id || request.headers.get('X-User-ID') || 'anonymous';
        const { topic_states = [], fsrs_cards = [] } = payload;

        // If DB binding is configured, execute D1 batch operations
        if (env.DB) {
          const stmts = [];

          // Upsert topic states
          for (const ts of topic_states) {
            stmts.push(
              env.DB.prepare(`
                INSERT INTO user_topic_states (user_id, topic_id, state, consecutive_passes, distinct_facets_json, acquired_via, last_review_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, topic_id) DO UPDATE SET
                  state = excluded.state,
                  consecutive_passes = excluded.consecutive_passes,
                  distinct_facets_json = excluded.distinct_facets_json,
                  acquired_via = excluded.acquired_via,
                  last_review_at = excluded.last_review_at,
                  updated_at = CURRENT_TIMESTAMP
              `).bind(
                userId,
                ts.topic_id,
                ts.state,
                ts.consecutive_passes || 0,
                JSON.stringify(ts.distinct_facets || []),
                ts.acquired_via || null,
                ts.last_review_at || null
              )
            );
          }

          // Upsert FSRS cards
          for (const card of fsrs_cards) {
            stmts.push(
              env.DB.prepare(`
                INSERT INTO user_fsrs_cards (user_id, card_id, state, due_at, stability, difficulty, step, reps, lapses, last_review_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, card_id) DO UPDATE SET
                  state = excluded.state,
                  due_at = excluded.due_at,
                  stability = excluded.stability,
                  difficulty = excluded.difficulty,
                  step = excluded.step,
                  reps = excluded.reps,
                  lapses = excluded.lapses,
                  last_review_at = excluded.last_review_at,
                  updated_at = CURRENT_TIMESTAMP
              `).bind(
                userId,
                card.card_id,
                card.state,
                card.due_at,
                card.stability,
                card.difficulty,
                card.step || 0,
                card.reps || 0,
                card.lapses || 0,
                card.last_review_at || null
              )
            );
          }

          if (stmts.length > 0) {
            await env.DB.batch(stmts);
          }
        }

        return jsonResponse({
          success: true,
          synced_topics: topic_states.length,
          synced_cards: fsrs_cards.length,
          synced_at: new Date().toISOString(),
        });
      } catch (err) {
        return jsonResponse({ error: err.message }, 400);
      }
    }

    // 4. Sync Pull Endpoint
    if (path === '/api/sync/pull' && request.method === 'GET') {
      const userId = url.searchParams.get('user_id') || request.headers.get('X-User-ID') || 'anonymous';
      const since = url.searchParams.get('since');

      let topicStates = [];
      let fsrsCards = [];

      if (env.DB) {
        let topicQuery = 'SELECT * FROM user_topic_states WHERE user_id = ?';
        let cardQuery = 'SELECT * FROM user_fsrs_cards WHERE user_id = ?';
        const params = [userId];

        if (since) {
          topicQuery += ' AND updated_at > ?';
          cardQuery += ' AND updated_at > ?';
          params.push(since);
        }

        const topicsRes = await env.DB.prepare(topicQuery).bind(...params).all();
        const cardsRes = await env.DB.prepare(cardQuery).bind(...params).all();

        topicStates = topicsRes.results || [];
        fsrsCards = cardsRes.results || [];
      }

      return jsonResponse({
        user_id: userId,
        since: since || null,
        pulled_at: new Date().toISOString(),
        topic_states: topicStates,
        fsrs_cards: fsrsCards,
      });
    }

    return jsonResponse({ error: 'Endpoint not found' }, 404);
  },
};
