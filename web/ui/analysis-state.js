/* Calculation identities and supersession. Scientific arrays are never transformed here. */
(() => {
  'use strict';
  class CalculationState {
    constructor() { this.revision = 0; this.key = null; this.result = null; this.status = 'empty'; }
    identity(snapshot) { return JSON.stringify(snapshot); }
    matches(snapshot) { return this.status === 'ready' && this.key === this.identity(snapshot); }
    begin(snapshot) { this.key = this.identity(snapshot); this.status = 'processing'; return {revision: ++this.revision, key: this.key}; }
    current(ticket, snapshot) { return ticket.revision === this.revision && ticket.key === this.identity(snapshot); }
    commit(ticket, snapshot, result) {
      if (!this.current(ticket, snapshot)) return false;
      this.result = result; this.status = 'ready'; return true;
    }
    invalidate() { ++this.revision; this.key = null; this.result = null; this.status = 'pending'; }
  }
  window.WaveGuardAnalysisState = CalculationState;
})();
