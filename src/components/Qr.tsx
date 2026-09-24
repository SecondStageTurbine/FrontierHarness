import qrcode from 'qrcode-generator';

/** A QR code for a phone camera: dark modules on white, whatever the theme, so it always scans. */
export function Qr({text,label}:{text:string;label:string}){
 const qr=qrcode(0,'M');qr.addData(text);qr.make();
 return <div className="qr" role="img" aria-label={label} dangerouslySetInnerHTML={{__html:qr.createSvgTag({cellSize:4,margin:2,scalable:true})}}/>;
}
