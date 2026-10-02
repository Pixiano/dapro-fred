precision mediump float;
uniform float time;
void main(){
  float glow = 0.5 + 0.5*sin(time*5.0);
  gl_FragColor = vec4(0.2, 0.82, 1.0, glow); // #3bd1ff color with pulsating alpha
}