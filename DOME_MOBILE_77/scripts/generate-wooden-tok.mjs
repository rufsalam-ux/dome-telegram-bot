import {writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

const sampleRate=44_100;
const seconds=.16;
const sampleCount=Math.round(sampleRate*seconds);
const pcm=Buffer.alloc(sampleCount*2);

for(let index=0;index<sampleCount;index+=1){
  const time=index/sampleRate;
  const attack=Math.min(1,time/.004);
  const body=Math.exp(-time*24);
  const knock=Math.sin(2*Math.PI*148*time)+.42*Math.sin(2*Math.PI*296*time)+.18*Math.sin(2*Math.PI*444*time);
  const value=Math.max(-1,Math.min(1,attack*body*knock*.54));
  pcm.writeInt16LE(Math.round(value*32_767),index*2);
}

const header=Buffer.alloc(44);
header.write('RIFF',0);header.writeUInt32LE(36+pcm.length,4);header.write('WAVE',8);
header.write('fmt ',12);header.writeUInt32LE(16,16);header.writeUInt16LE(1,20);header.writeUInt16LE(1,22);
header.writeUInt32LE(sampleRate,24);header.writeUInt32LE(sampleRate*2,28);header.writeUInt16LE(2,32);header.writeUInt16LE(16,34);
header.write('data',36);header.writeUInt32LE(pcm.length,40);

const output=fileURLToPath(new URL('../assets/sounds/wooden-tok.wav',import.meta.url));
writeFileSync(output,Buffer.concat([header,pcm]));
console.log(`Generated ${output} (${sampleCount} samples)`);
