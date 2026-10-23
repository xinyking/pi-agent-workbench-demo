import {enhanceSelect} from './workspace-picker.mjs';
const $=id=>document.getElementById(id);
export function initPreferences(){
function applyFontScale(value){const scale=['0.9','1','1.1','1.2'].includes(value)?value:'1';document.documentElement.style.setProperty('--font-scale',scale);$('font-scale').value=scale;enhanceSelect($('font-scale'));try{localStorage.setItem('workbench-font-scale',scale);}catch{}}
let savedFontScale='1';try{savedFontScale=localStorage.getItem('workbench-font-scale')||'1';}catch{}
applyFontScale(savedFontScale);$('font-scale').onchange=()=>applyFontScale($('font-scale').value);
}
