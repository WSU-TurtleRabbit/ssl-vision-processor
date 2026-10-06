// SPDX-License-Identifier: Apache-2.0
// Phase-0 probe: what does PoCL return for convert_uchar_sat/convert_char_sat(float) and native_sqrt?
// Build & run (OpenCL dev headers + ICD required):
//   g++ -O1 -ffp-contract=off -std=c++17 cuda/probes/pocl_convert_probe.cpp -lOpenCL -o /tmp/probe && /tmp/probe
// Results are recorded in cuda/NOTES.md.
#define CL_HPP_TARGET_OPENCL_VERSION 300
#include <CL/opencl.hpp>
#include <cstdio>
#include <cmath>
#include <vector>
static const char* src = R"CL(
kernel void cv(global const float* in, global uchar* out, global char* outc) {
  int i = get_global_id(0);
  out[i] = convert_uchar_sat(in[i]);
  outc[i] = convert_char_sat(in[i]);
}
kernel void ns(global const float* in, global float* out) {
  int i = get_global_id(0);
  out[i] = native_sqrt(in[i]);
}
)CL";
int main(){
  cl::Context ctx(CL_DEVICE_TYPE_ALL); auto dev = ctx.getInfo<CL_CONTEXT_DEVICES>()[0];
  cl::CommandQueue q(ctx, dev);
  cl::Program p(ctx, src); if(p.build({dev})!=CL_SUCCESS){printf("%s\n",p.getBuildInfo<CL_PROGRAM_BUILD_LOG>(dev).c_str());return 1;}
  std::vector<float> v={-300.f,-1.5f,-0.99f,-0.5f,-0.f,0.4f,0.5f,0.6f,1.5f,2.5f,126.5f,127.7f,128.5f,254.5f,254.99f,255.f,255.5f,256.f,1e9f,NAN,INFINITY,-INFINITY};
  int N=v.size();
  cl::Buffer bi(ctx,CL_MEM_READ_ONLY|CL_MEM_COPY_HOST_PTR,N*4,v.data()), bo(ctx,CL_MEM_WRITE_ONLY,N), bc(ctx,CL_MEM_WRITE_ONLY,N);
  cl::Kernel k(p,"cv"); k.setArg(0,bi);k.setArg(1,bo);k.setArg(2,bc); q.enqueueNDRangeKernel(k,cl::NullRange,cl::NDRange(N));
  std::vector<uint8_t> o(N); std::vector<int8_t> oc(N); q.enqueueReadBuffer(bo,true,0,N,o.data()); q.enqueueReadBuffer(bc,true,0,N,oc.data());
  for(int i=0;i<N;i++) printf("convert_uchar_sat(%g)=%u convert_char_sat=%d\n",v[i],o[i],oc[i]);
  // native_sqrt vs correctly rounded sqrtf
  std::vector<float> s(1<<20); for(size_t i=0;i<s.size();i++) s[i]=(float)i*0.37f+ (float)(i%7)/3.f;
  cl::Buffer si(ctx,CL_MEM_READ_ONLY|CL_MEM_COPY_HOST_PTR,s.size()*4,s.data()), so(ctx,CL_MEM_WRITE_ONLY,s.size()*4);
  cl::Kernel k2(p,"ns"); k2.setArg(0,si);k2.setArg(1,so); q.enqueueNDRangeKernel(k2,cl::NullRange,cl::NDRange(s.size()));
  std::vector<float> r(s.size()); q.enqueueReadBuffer(so,true,0,s.size()*4,r.data());
  long bad=0; for(size_t i=0;i<s.size();i++) if(r[i]!=sqrtf(s[i])) bad++;
  printf("native_sqrt != correctly-rounded sqrtf: %ld / %zu\n",bad,s.size());
}
