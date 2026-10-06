// SPDX-License-Identifier: Apache-2.0
// Phase-0 probe: what does PoCL return for read_imageui() with CLK_FILTER_LINEAR on CL_RGBA/CL_UNSIGNED_INT8 images (undefined by the OpenCL spec), CL_R support and scalar image writes?
// Build & run (OpenCL dev headers + ICD required):
//   g++ -O1 -ffp-contract=off -std=c++17 cuda/probes/pocl_sampler_probe.cpp -lOpenCL -o /tmp/probe && /tmp/probe
// Results are recorded in cuda/NOTES.md.
#define CL_HPP_TARGET_OPENCL_VERSION 300
#include <CL/opencl.hpp>
#include <cstdio>
#include <cmath>
#include <vector>
#include <array>
#include <random>
static const char* src = R"CL(
const sampler_t sl = CLK_FILTER_LINEAR | CLK_NORMALIZED_COORDS_FALSE | CLK_ADDRESS_CLAMP_TO_EDGE;
const sampler_t sn = CLK_FILTER_NEAREST | CLK_NORMALIZED_COORDS_FALSE | CLK_ADDRESS_CLAMP_TO_EDGE;
kernel void probe(read_only image2d_t img, global const float2* coords, global uint* outL, global uint* outN) {
  int i = get_global_id(0);
  uint4 v = read_imageui(img, sl, coords[i]);
  outL[4*i]=v.x; outL[4*i+1]=v.y; outL[4*i+2]=v.z; outL[4*i+3]=v.w;
  uint4 n = read_imageui(img, sn, coords[i]);
  outN[i]=n.x;
}
kernel void lint(read_only image2d_t img, global uint* out) {
  int2 pos = (int2)(get_global_id(0)-2, get_global_id(1)-2);
  uint4 v = read_imageui(img, sl, pos);
  int i = get_global_id(0) + get_global_id(1)*get_global_size(0);
  out[4*i]=v.x; out[4*i+1]=v.y; out[4*i+2]=v.z; out[4*i+3]=v.w;
}
kernel void wr(write_only image2d_t img, uint val) { write_imageui(img, (int2)(0,0), val); }
kernel void wrf(write_only image2d_t img, float val) { write_imagef(img, (int2)(0,0), val); }
kernel void rdint(read_only image2d_t img, global uint* out) {
  const sampler_t s2 = CLK_FILTER_NEAREST | CLK_NORMALIZED_COORDS_FALSE | CLK_ADDRESS_NONE;
  uint4 v = read_imageui(img, s2, (int2)(0,0)); out[0]=v.x; out[1]=v.y; out[2]=v.z; out[3]=v.w;
}
)CL";
int main(){
  cl::Context ctx(CL_DEVICE_TYPE_ALL); auto dev = ctx.getInfo<CL_CONTEXT_DEVICES>()[0];
  printf("device: %s\n", dev.getInfo<CL_DEVICE_NAME>().c_str());
  cl::CommandQueue q(ctx, dev);
  cl::Program p(ctx, src); if(p.build({dev})!=CL_SUCCESS){printf("%s\n",p.getBuildInfo<CL_PROGRAM_BUILD_LOG>(dev).c_str());return 1;}
  const int W=16,H=16; std::mt19937 rng(1);
  std::vector<uint8_t> px(W*H*4); for(auto&v:px) v=rng()&255;
  cl::Image2D img(ctx, CL_MEM_READ_ONLY|CL_MEM_COPY_HOST_PTR, cl::ImageFormat(CL_RGBA,CL_UNSIGNED_INT8), W,H,0,px.data());
  int err; cl::Image2D r(ctx, CL_MEM_READ_WRITE, cl::ImageFormat(CL_R,CL_UNSIGNED_INT8), W,H,0,nullptr,&err); printf("CL_R U8 create err=%d\n",err);
  cl::Image2D rf(ctx, CL_MEM_READ_WRITE, cl::ImageFormat(CL_R,CL_FLOAT), W,H,0,nullptr,&err); printf("CL_R F32 create err=%d\n",err);
  std::array<cl::size_type,3> org{0,0,0}, reg{1,1,1};
  { cl::Image2D w(ctx, CL_MEM_READ_WRITE, cl::ImageFormat(CL_RGBA,CL_UNSIGNED_INT8), 1,1); cl::Kernel k(p,"wr"); k.setArg(0,w); k.setArg(1,(cl_uint)77);
    q.enqueueNDRangeKernel(k,cl::NullRange,cl::NDRange(1)); uint8_t o[4]; q.enqueueReadImage(w,true,org,reg,0,0,o); printf("write_imageui(uint 77) on RGBA8 -> %d %d %d %d\n",o[0],o[1],o[2],o[3]); }
  { cl::Image2D w(ctx, CL_MEM_READ_WRITE, cl::ImageFormat(CL_RGBA,CL_FLOAT), 1,1); cl::Kernel k(p,"wrf"); k.setArg(0,w); k.setArg(1,1.5f);
    q.enqueueNDRangeKernel(k,cl::NullRange,cl::NDRange(1)); float o[4]; q.enqueueReadImage(w,true,org,reg,0,0,o); printf("write_imagef(float 1.5) on RGBAF -> %g %g %g %g\n",o[0],o[1],o[2],o[3]); }
  { uint8_t v[4]={11,22,33,44}; cl::Image2D w(ctx, CL_MEM_READ_WRITE|CL_MEM_COPY_HOST_PTR, cl::ImageFormat(CL_RGBA,CL_UNSIGNED_INT8), 1,1,0,v); cl::Buffer o(ctx,CL_MEM_WRITE_ONLY,16);
    cl::Kernel k(p,"rdint"); k.setArg(0,w); k.setArg(1,o); q.enqueueNDRangeKernel(k,cl::NullRange,cl::NDRange(1)); uint32_t r4[4]; q.enqueueReadBuffer(o,true,0,16,r4); printf("read_imageui RGBA8 {11,22,33,44} -> %u %u %u %u\n",r4[0],r4[1],r4[2],r4[3]); }
  { cl::Buffer o(ctx,CL_MEM_WRITE_ONLY,(W+4)*(H+4)*16); cl::Kernel k(p,"lint"); k.setArg(0,img); k.setArg(1,o); q.enqueueNDRangeKernel(k,cl::NullRange,cl::NDRange(W+4,H+4));
    std::vector<uint32_t> r((W+4)*(H+4)*4); q.enqueueReadBuffer(o,true,0,r.size()*4,r.data()); long bad=0, badbil=0;
    for(int y=0;y<H+4;y++)for(int x=0;x<W+4;x++)for(int ch=0;ch<4;ch++){ int xx=std::min(std::max(x-2,0),W-1), yy=std::min(std::max(y-2,0),H-1); if((int)r[(x+y*(W+4))*4+ch]!=px[(xx+yy*W)*4+ch]) bad++; }
    printf("LINEAR sampler with int2 coords (incl. out of range, clamp-to-edge) != clamped direct pixel: %ld\n",bad); }
  std::vector<float> c;
  float fr[]={0.f,0.25f,0.5f,0.75f,-0.25f,0.1f,0.4f,0.6f,0.9f,0.49999f,0.50001f,0.3f,0.7f,0.125f,0.375f,0.625f,0.875f, 0.33f, 0.66f};
  for(float fx:fr)for(float fy:fr)for(int y=-2;y<=H+1;y++)for(int x=-2;x<=W+1;x++){c.push_back(x+fx);c.push_back(y+fy);}
  for(int i=0;i<3000000;i++){c.push_back((rng()%1000000)/1000000.f*(W+4)-2.f); c.push_back((rng()%1000000)/1000000.f*(H+4)-2.f);}
  // large coordinates like a real 1280x720 frame (exercise float precision)
  for(int k=0;k<4;k++){ float base[]={0.5f,-0.5f,1.5f,0.25f}; for(float b0:base){ float u=b0; for(int s=0;s<40;s++){ c.push_back(u); c.push_back(u); c.push_back(u); c.push_back(3.3f); c.push_back(3.3f); c.push_back(u); u=nextafterf(u, k%2? 10.f : -10.f);} } }
  int N=c.size()/2;
  cl::Buffer bc(ctx, CL_MEM_READ_ONLY|CL_MEM_COPY_HOST_PTR, c.size()*4, c.data());
  cl::Buffer bl(ctx, CL_MEM_WRITE_ONLY, N*16), bn(ctx, CL_MEM_WRITE_ONLY, N*4);
  cl::Kernel k(p,"probe"); k.setArg(0,img);k.setArg(1,bc);k.setArg(2,bl);k.setArg(3,bn);
  q.enqueueNDRangeKernel(k,cl::NullRange,cl::NDRange(N));
  std::vector<uint32_t> L(N*4), Nn(N); q.enqueueReadBuffer(bl,true,0,N*16,L.data()); q.enqueueReadBuffer(bn,true,0,N*4,Nn.data());
  auto at=[&](int x,int y,int ch){x=std::min(std::max(x,0),W-1);y=std::min(std::max(y,0),H-1);return (int)px[(x+y*W)*4+ch];};
  const char* names[]={"bilinear no-fma trunc","bilinear fma-chain trunc","lerp trunc","bilinear float round-half-up","bilinear float rint(half-even)","nearest floor(u)","nearest floor(u+0.5)"};
  const int NH=7; long bad[NH]={0}; long ex[NH]; for(int h=0;h<NH;h++)ex[h]=-1;
  for(int i=0;i<N;i++){ float u=c[2*i],v=c[2*i+1];
    for(int ch=0;ch<4;ch++){ int got=L[4*i+ch]; int pred[NH];
      pred[5]=at((int)floorf(u),(int)floorf(v),ch);
      pred[6]=at((int)floorf(u+0.5f),(int)floorf(v+0.5f),ch);
      float uu=u-0.5f,vv=v-0.5f; int i0=(int)floorf(uu),j0=(int)floorf(vv); float a=uu-i0,b=vv-j0;
      float bil=(1-a)*(1-b)*at(i0,j0,ch)+a*(1-b)*at(i0+1,j0,ch)+(1-a)*b*at(i0,j0+1,ch)+a*b*at(i0+1,j0+1,ch);
      float w00=(1-a)*(1-b), w10=a*(1-b), w01=(1-a)*b, w11=a*b;
      float t00=at(i0,j0,ch),t10=at(i0+1,j0,ch),t01=at(i0,j0+1,ch),t11=at(i0+1,j0+1,ch);
      float nofma = ((w00*t00 + w10*t10) + w01*t01) + w11*t11;
      volatile float p0=w00*t00, p1=w10*t10, p2=w01*t01, p3=w11*t11; float nofma2=((p0+p1)+p2)+p3;
      float withfma = fmaf(w11,t11,fmaf(w01,t01,fmaf(w10,t10,w00*t00)));
      float lerp1 = t00 + a*(t10-t00), lerp2 = t01 + a*(t11-t01); float lerpv = lerp1 + b*(lerp2-lerp1);
      pred[0]=(int)nofma2; pred[1]=(int)withfma; pred[2]=(int)lerpv; pred[3]=(int)floorf(bil+0.5f); pred[4]=(int)rintf(bil); (void)nofma;
      for(int h=0;h<NH;h++) if(pred[h]!=got){bad[h]++; if(ex[h]<0)ex[h]=i*4+ch;}
    }}
  printf("LINEAR read_imageui, N=%d coords x 4 channels\n",N);
  for(int h=0;h<NH;h++){printf("  %-34s mismatches=%ld",names[h],bad[h]); if(ex[h]>=0){int i=ex[h]/4; printf("  e.g. (%.5f,%.5f) ch%ld got %u", c[2*i],c[2*i+1],ex[h]%4,L[ex[h]]);} printf("\n");}
  long nb=0; for(int i=0;i<N;i++) if((int)Nn[i]!=at((int)floorf(c[2*i]),(int)floorf(c[2*i+1]),0)) nb++; printf("NEAREST read_imageui(float2) vs floor: %ld mismatches\n",nb);
  long same=0; for(int i=0;i<N;i++) if(Nn[i]==L[4*i]) same++; printf("LINEAR==NEAREST on ch0: %ld/%d\n",same,N);
}
