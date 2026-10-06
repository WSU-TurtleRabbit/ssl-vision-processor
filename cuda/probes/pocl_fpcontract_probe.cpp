// SPDX-License-Identifier: Apache-2.0
// Phase-0 probe: what does PoCL return for implicit FP contraction (a*b+c -> fma) in kernels and division rounding?
// Build & run (OpenCL dev headers + ICD required):
//   g++ -O1 -ffp-contract=off -std=c++17 cuda/probes/pocl_fpcontract_probe.cpp -lOpenCL -o /tmp/probe && /tmp/probe
// Results are recorded in cuda/NOTES.md.
#define CL_HPP_TARGET_OPENCL_VERSION 300
#include <CL/opencl.hpp>
#include <cstdio>
#include <cmath>
#include <vector>
#include <random>
// Does PoCL contract a*b+c into fma (OpenCL C FP_CONTRACT default ON)? Within one expression / across statements / vector ops?
static const char* src = R"CL(
kernel void k(global const float* a, global const float* b, global const float* c, global float* o1, global float* o2, global float* o3, global float* o4) {
  int i = get_global_id(0);
  o1[i] = a[i]*b[i] + c[i];
  float t = a[i]*b[i];
  o2[i] = t + c[i];
  float2 v = (float2)(a[i], c[i]); float2 w = (float2)(b[i], b[i]);
  float2 r = v*w + (float2)(c[i], a[i]);
  o3[i] = a[i]*b[i] + c[i]*b[i];
  o4[i] = a[i] / b[i];
}
)CL";
int main(){
  cl::Context ctx(CL_DEVICE_TYPE_ALL); auto dev = ctx.getInfo<CL_CONTEXT_DEVICES>()[0];
  cl::CommandQueue q(ctx, dev);
  for(const char* opts : {"", "-cl-fast-relaxed-math"}) {
  cl::Program p(ctx, src); if(p.build({dev}, opts)!=CL_SUCCESS){printf("%s\n",p.getBuildInfo<CL_PROGRAM_BUILD_LOG>(dev).c_str());return 1;}
  const int N=1<<20; std::mt19937 rng(3); std::uniform_real_distribution<float> d(-1000.f,1000.f);
  std::vector<float> a(N),b(N),c(N); for(int i=0;i<N;i++){a[i]=d(rng);b[i]=d(rng);c[i]=d(rng);}
  cl::Buffer ba(ctx,CL_MEM_READ_ONLY|CL_MEM_COPY_HOST_PTR,(size_t)N*4,a.data()), bb(ctx,CL_MEM_READ_ONLY|CL_MEM_COPY_HOST_PTR,(size_t)N*4,b.data()), bc(ctx,CL_MEM_READ_ONLY|CL_MEM_COPY_HOST_PTR,(size_t)N*4,c.data());
  cl::Buffer o[4]={cl::Buffer(ctx,CL_MEM_WRITE_ONLY,(size_t)N*4),cl::Buffer(ctx,CL_MEM_WRITE_ONLY,(size_t)N*4),cl::Buffer(ctx,CL_MEM_WRITE_ONLY,(size_t)N*4),cl::Buffer(ctx,CL_MEM_WRITE_ONLY,(size_t)N*4)};
  cl::Kernel k(p,"k"); k.setArg(0,ba);k.setArg(1,bb);k.setArg(2,bc); for(int j=0;j<4;j++)k.setArg(3+j,o[j]);
  q.enqueueNDRangeKernel(k,cl::NullRange,cl::NDRange(N));
  std::vector<float> r[4]; for(int j=0;j<4;j++){r[j].resize(N); q.enqueueReadBuffer(o[j],true,0,(size_t)N*4,r[j].data());}
  long eqFma[3]={0},eqNo[3]={0}, divOk=0;
  for(int i=0;i<N;i++){ volatile float m=a[i]*b[i]; float no=m+c[i]; float f=fmaf(a[i],b[i],c[i]);
    for(int j=0;j<2;j++){ if(r[j][i]==f) eqFma[j]++; if(r[j][i]==no) eqNo[j]++; }
    { volatile float ab=a[i]*b[i], cb=c[i]*b[i]; float L=fmaf(a[i],b[i],cb), R=fmaf(c[i],b[i],ab); if(r[2][i]==L) eqFma[2]++; if(r[2][i]==R) eqNo[2]++; }
    volatile float dq=a[i]/b[i]; if(r[3][i]==dq) divOk++; }
  printf("opts='%s'\n", opts);
  const char* nm[]={"a*b+c (one expr)","t=a*b; t+c","a*b + c*b"};
  for(int j=0;j<3;j++) printf("  %-20s ==fma(lhs) %ld  ==unfused/fma(rhs) %ld  (N=%d)\n",nm[j],eqFma[j],eqNo[j],N);
  printf("  a/b correctly rounded: %ld/%d\n",divOk,N);
  }
}
