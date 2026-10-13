// The microphone's samples as they come: the level all the time, the samples themselves while recording.
// Chunks of 4096 samples go to the page, which writes the take as a WAV (no lossy codec on the way).
class Recorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.recording = false;
    this.buf = new Float32Array(4096);
    this.fill = 0;
    this.peak = 0;
    this.sum = 0;
    this.count = 0;
    this.port.onmessage = (e) => {
      if (e.data === 'start') {
        this.fill = 0;
        this.recording = true;
      } else if (e.data === 'stop') {
        this.recording = false;
        if (this.fill > 0) {
          this.port.postMessage({type: 'chunk', data: this.buf.slice(0, this.fill)});
        }
        this.fill = 0;
        this.port.postMessage({type: 'done'});
      }
    };
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) {
      return true;
    }
    for (let i = 0; i < ch.length; i++) {
      const a = Math.abs(ch[i]);
      if (a > this.peak) this.peak = a;
      this.sum += ch[i] * ch[i];
    }
    this.count += ch.length;
    if (this.count >= 2048) {
      this.port.postMessage({type: 'level', peak: this.peak, rms: Math.sqrt(this.sum / this.count)});
      this.peak = 0;
      this.sum = 0;
      this.count = 0;
    }
    if (this.recording) {
      for (let i = 0; i < ch.length; i++) {
        this.buf[this.fill++] = ch[i];
        if (this.fill === this.buf.length) {
          this.port.postMessage({type: 'chunk', data: this.buf.slice(0)});
          this.fill = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor('recorder', Recorder);
