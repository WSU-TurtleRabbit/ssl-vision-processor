#    Copyright 2024 Felix Weinmann
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

cmake_minimum_required(VERSION 3.22)

# C++20 needs GCC >= 10; Ubuntu 20.04 (JetPack 5) defaults to GCC 9, so prefer g++-10 when it is installed
if(NOT DEFINED CMAKE_CXX_COMPILER AND NOT DEFINED ENV{CXX} AND EXISTS "/usr/bin/g++-10")
    execute_process(COMMAND g++ -dumpversion OUTPUT_VARIABLE VP_DEFAULT_GCC_VERSION OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
    if(VP_DEFAULT_GCC_VERSION VERSION_LESS 10)
        set(CMAKE_C_COMPILER "/usr/bin/gcc-10")
        set(CMAKE_CXX_COMPILER "/usr/bin/g++-10")
        if(NOT DEFINED CMAKE_CUDA_HOST_COMPILER)
            set(CMAKE_CUDA_HOST_COMPILER "/usr/bin/g++-10")
        endif()
    endif()
endif()

project(vision_processor)
enable_language(ASM)

option(WITH_SPINNAKER "Build with Spinnaker support (if library available)" ON)
option(WITH_MVIMPACT "Build with mvIMPACT/Bluefox support (if library available)" ON)

# CUDA backend: enabled by default on aarch64 (Jetson) when a CUDA compiler is found, opt-in elsewhere
include(CheckLanguage)
if(NOT DEFINED CMAKE_CUDA_COMPILER AND NOT DEFINED ENV{CUDACXX} AND EXISTS "/usr/local/cuda/bin/nvcc")
    set(CMAKE_CUDA_COMPILER "/usr/local/cuda/bin/nvcc")
endif()
check_language(CUDA)
if(CMAKE_CUDA_COMPILER AND CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
    set(WITH_CUDA_DEFAULT ON)
else()
    set(WITH_CUDA_DEFAULT OFF)
endif()
option(WITH_CUDA "Build the CUDA compute backend targets" ${WITH_CUDA_DEFAULT})
# This fork targets the Jetson: with CUDA available the (CPU-only, PoCL) OpenCL targets are not built unless requested
if(WITH_CUDA)
    option(WITH_OPENCL "Build the OpenCL compute backend targets" OFF)
else()
    option(WITH_OPENCL "Build the OpenCL compute backend targets" ON)
endif()
if(WITH_CUDA)
    if(NOT CMAKE_CUDA_COMPILER)
        message(FATAL_ERROR "WITH_CUDA=ON but no CUDA compiler found (set CMAKE_CUDA_COMPILER or CUDACXX)")
    endif()
    if(NOT DEFINED CMAKE_CUDA_ARCHITECTURES)
        set(CMAKE_CUDA_ARCHITECTURES 87) # Jetson AGX Orin
    endif()
    enable_language(CUDA)
    find_package(CUDAToolkit REQUIRED)
    message(STATUS "CUDA backend enabled (${CMAKE_CUDA_COMPILER_VERSION}, sm_${CMAKE_CUDA_ARCHITECTURES})")
endif()

# Adapted from https://stackoverflow.com/a/48832234 CC BY-SA 4.0 by einpoklum
if (NOT CMAKE_BUILD_TYPE OR CMAKE_BUILD_TYPE STREQUAL "")
    set(CMAKE_BUILD_TYPE "RelWithDebInfo" CACHE STRING "" FORCE)
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -march=native -ftree-vectorize -ffast-math")
add_compile_options("$<$<NOT:$<COMPILE_LANGUAGE:CUDA>>:-Wall>")
list(APPEND CMAKE_MODULE_PATH ${PROJECT_SOURCE_DIR}/cmake)

find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBAV REQUIRED IMPORTED_TARGET libavformat libavcodec libavutil)

find_package(Eigen3 REQUIRED)
find_package(mvIMPACT)
if(WITH_OPENCL AND WITH_CUDA)
    find_package(OpenCL)
    if(NOT OpenCL_FOUND)
        message(STATUS "OpenCL not found, building only the CUDA backend")
        set(WITH_OPENCL OFF)
    endif()
elseif(WITH_OPENCL)
    find_package(OpenCL REQUIRED)
endif()
if(NOT WITH_OPENCL AND NOT WITH_CUDA)
    message(FATAL_ERROR "At least one compute backend (WITH_OPENCL, WITH_CUDA) is required")
endif()
find_package(OpenCV REQUIRED COMPONENTS core imgproc imgcodecs videoio)
find_package(Spinnaker)

# StereoLabs ZED SDK (camera driver "ZED"), needs the CUDA backend. The SDK's own zed-config.cmake uses the
# removed FindCUDA module, so the library is located directly.
set(ZED_DIR "/usr/local/zed" CACHE PATH "ZED SDK installation directory")
if(WITH_CUDA AND EXISTS "${ZED_DIR}/include/sl/Camera.hpp")
    set(WITH_ZED_DEFAULT ON)
else()
    set(WITH_ZED_DEFAULT OFF)
endif()
option(WITH_ZED "Build with ZED SDK camera support (requires WITH_CUDA)" ${WITH_ZED_DEFAULT})
if(WITH_ZED)
    if(NOT WITH_CUDA)
        message(FATAL_ERROR "WITH_ZED=ON requires WITH_CUDA=ON")
    endif()
    find_library(ZED_LIBRARY sl_zed HINTS "${ZED_DIR}/lib" REQUIRED)
    message(STATUS "ZED SDK found: ${ZED_LIBRARY}")
    # The SDK headers use constructor template-ids (Vector2<T>(...)) that C++20 forbids and GCC >= 11 rejects;
    # only this file includes them
    set_source_files_properties(src/driver/zeddriver.cpp PROPERTIES COMPILE_OPTIONS "-std=gnu++17")
endif()
find_package(yaml-cpp REQUIRED)

# Protobuf links to Abseil starting with 4.22.0. The FindProtobuf module does
# not yet account for this, so we need to use Config mode instead. However,
# Protobuf does not provide a Config CMake script on old versions, so we need
# to fallback to Module mode in that case.
find_package(Protobuf CONFIG QUIET)
if (Protobuf_FOUND)
    message(STATUS "Found Protobuf ${Protobuf_VERSION} via Config mode")
else()
    find_package(Protobuf REQUIRED)
    message(STATUS "Found Protobuf ${Protobuf_VERSION} via Module mode")
endif()


add_compile_definitions(CL_HPP_TARGET_OPENCL_VERSION=300)
# m (c math) and stdc++ (c++ standard library) explicitly required by LLD
link_libraries("m" "stdc++" ${YAML_CPP_LIBRARIES} ${OpenCV_LIBS} PkgConfig::LIBAV ${SPINNAKER_LIBS} Eigen3::Eigen protobuf::libprotobuf ${mvIMPACT_LIBS})
include_directories(SYSTEM ${YAML_INCLUDE_DIRS} ${OpenCV_INCLUDE_DIRS} ${SPINNAKER_INCLUDE_DIRS} ${mvIMPACT_INCLUDE_DIRS})

include_directories(src)
file(GLOB PROTO_FILES proto/*.proto)
file(GLOB CL_KERNELS RELATIVE "${CMAKE_SOURCE_DIR}" kernel/*.cl)
file(GLOB_RECURSE SRC src/*.cpp src/*.c)
list(REMOVE_ITEM SRC "${CMAKE_SOURCE_DIR}/src/main.cpp" "${CMAKE_SOURCE_DIR}/src/geometry_benchmark.cpp" "${CMAKE_SOURCE_DIR}/src/blob_benchmark.cpp" "${CMAKE_SOURCE_DIR}/src/opencl.cpp")

# Adapted from https://stackoverflow.com/a/56006001 CC BY-SA 4.0 by Itay Grudev
# Adapted from https://stackoverflow.com/a/4910421 CC BY-SY 4.0 by John Ripley
set(CL_KERNEL_HEADER "//Autogenerated by CMake, edits will be overridden\n#pragma once\n")
set(CL_KERNEL_ASM ".section .rodata\n") # # Autogenerated by CMake, edits will be overridden\n
foreach(input_file IN LISTS CL_KERNELS)
    cmake_path(GET input_file FILENAME input_filename)
    string(REPLACE . _ input_file_cname "${input_file}")
    string(REPLACE / _ input_file_cname "${input_file_cname}")
    string(REPLACE . _ input_filename_cname "${input_filename}")

    list(APPEND CL_KERNEL_PATHS "${CMAKE_SOURCE_DIR}/${input_file}")
    string(APPEND CL_KERNEL_ASM ".global kernel_${input_filename_cname}\n.type kernel_${input_filename_cname}, @object\nkernel_${input_filename_cname}:\n\t.incbin \"${CMAKE_SOURCE_DIR}/${input_file}\"\n\t.byte 0\n\n")
    string(APPEND CL_KERNEL_HEADER "extern const char kernel_${input_filename_cname}[];\n")
endforeach()

# Don't override cl_kernels.h or cl_kernels.S if already up-to-date to prevent unnecessary recompilation
if(EXISTS "${CMAKE_SOURCE_DIR}/src/cl_kernels.h")
    file(READ "${CMAKE_SOURCE_DIR}/src/cl_kernels.h" CL_KERNEL_HEADER_OLD)
endif()
if(NOT "${CL_KERNEL_HEADER_OLD}" STREQUAL "${CL_KERNEL_HEADER}")
    file(WRITE "${CMAKE_SOURCE_DIR}/src/cl_kernels.h" "${CL_KERNEL_HEADER}")
    message(STATUS "(Re-)Generated cl_kernels.h")
endif()

if(EXISTS "src/cl_kernels.S")
    file(READ "src/cl_kernels.S" CL_KERNEL_ASM_OLD)
endif()
if(NOT "${CL_KERNEL_ASM_OLD}" STREQUAL "${CL_KERNEL_ASM}")
    file(WRITE "src/cl_kernels.S" "${CL_KERNEL_ASM}")
    message(STATUS "(Re-)Generated cl_kernels.S")
endif()

add_custom_target(AUTOGENERATE DEPENDS "src/cl_kernels.S")
set_property(SOURCE "src/cl_kernels.S" APPEND PROPERTY OBJECT_DEPENDS ${CL_KERNEL_PATHS})
protobuf_generate(TARGET AUTOGENERATE LANGUAGE cpp PROTOC_OUT_DIR "${CMAKE_SOURCE_DIR}/src" PROTOS ${PROTO_FILES})
get_property(PROTO_SRCS TARGET AUTOGENERATE PROPERTY SOURCES)
list(APPEND SRC ${PROTO_SRCS} "src/cl_kernels.S")

# Compute backends: every backend builds the full set of executables. When multiple backends are enabled,
# the OpenCL targets get the suffix _opencl (see cuda/NOTES.md).
if(WITH_CUDA)
    set(OPENCL_SUFFIX "_opencl")
else()
    set(OPENCL_SUFFIX "")
endif()

if(WITH_OPENCL)
    set(OPENCL_SRC ${SRC} "src/opencl.cpp")
    add_executable(${PROJECT_NAME}${OPENCL_SUFFIX} ${OPENCL_SRC} "src/main.cpp")
    add_executable("geometry_benchmark${OPENCL_SUFFIX}" ${OPENCL_SRC} "src/geometry_benchmark.cpp")
    add_executable("blob_benchmark${OPENCL_SUFFIX}" ${OPENCL_SRC} "src/blob_benchmark.cpp")

    foreach(target ${PROJECT_NAME}${OPENCL_SUFFIX} "geometry_benchmark${OPENCL_SUFFIX}" "blob_benchmark${OPENCL_SUFFIX}")
        add_dependencies(${target} AUTOGENERATE)
        target_link_libraries(${target} OpenCL::OpenCL)
    endforeach()

    install(TARGETS ${PROJECT_NAME}${OPENCL_SUFFIX} DESTINATION /usr/local/bin)
endif()

if(WITH_CUDA)
    # Table mapping the embedded kernel sources to their names, used by the CUDA OpenCL::compile() to identify kernels
    set(CUDA_KERNEL_TABLE "//Autogenerated by CMake, edits will be overridden\n#pragma once\n#include \"cl_kernels.h\"\n\nstruct VpCudaKernelTableEntry { const char* code; const char* name; };\nstatic const VpCudaKernelTableEntry vpCudaKernelTable[] = {\n")
    foreach(input_file IN LISTS CL_KERNELS)
        cmake_path(GET input_file FILENAME input_filename)
        cmake_path(GET input_file STEM input_stem)
        string(REPLACE . _ input_filename_cname "${input_filename}")
        string(APPEND CUDA_KERNEL_TABLE "\t{kernel_${input_filename_cname}, \"${input_stem}\"},\n")
    endforeach()
    string(APPEND CUDA_KERNEL_TABLE "};\n")
    file(CONFIGURE OUTPUT "${CMAKE_BINARY_DIR}/generated/cuda_kernel_table.h" CONTENT "${CUDA_KERNEL_TABLE}" @ONLY)

    file(GLOB CUDA_BACKEND_SRC cuda/src/*.cpp cuda/kernels/*.cu)
    add_library(vp_cuda_backend OBJECT ${CUDA_BACKEND_SRC})
    add_dependencies(vp_cuda_backend AUTOGENERATE)
    target_include_directories(vp_cuda_backend PUBLIC cuda/include PRIVATE cuda/kernels "${CMAKE_BINARY_DIR}/generated")
    target_compile_definitions(vp_cuda_backend PUBLIC VP_BACKEND_CUDA)
    # Exact IEEE float semantics; contractions PoCL performs are written explicitly with __fmaf_rn (see cuda/NOTES.md)
    target_compile_options(vp_cuda_backend PRIVATE $<$<COMPILE_LANGUAGE:CUDA>:-fmad=false --prec-div=true --prec-sqrt=true --ftz=false -Xcompiler=-Wall>)
    set_target_properties(vp_cuda_backend PROPERTIES CUDA_STANDARD 17 CUDA_STANDARD_REQUIRED ON)
    target_link_libraries(vp_cuda_backend PUBLIC CUDA::cudart)

    add_executable(${PROJECT_NAME} ${SRC} "src/main.cpp")
    add_executable("geometry_benchmark" ${SRC} "src/geometry_benchmark.cpp")
    add_executable("blob_benchmark" ${SRC} "src/blob_benchmark.cpp")

    foreach(target ${PROJECT_NAME} "geometry_benchmark" "blob_benchmark")
        add_dependencies(${target} AUTOGENERATE)
        target_link_libraries(${target} vp_cuda_backend)
        if(WITH_ZED)
            target_compile_definitions(${target} PRIVATE ZED_SDK)
            target_include_directories(${target} SYSTEM PRIVATE "${ZED_DIR}/include")
            target_link_libraries(${target} ${ZED_LIBRARY} CUDA::cuda_driver)
        endif()
    endforeach()

    install(TARGETS ${PROJECT_NAME} DESTINATION /usr/local/bin)

    # Per-kernel comparison harness (cuda/tools/kernel_compare.cpp), built once per backend
    add_executable(kernel_compare cuda/tools/kernel_compare.cpp "src/cl_kernels.S")
    add_dependencies(kernel_compare AUTOGENERATE)
    target_link_libraries(kernel_compare vp_cuda_backend)
    if(WITH_OPENCL)
        add_executable(kernel_compare_opencl cuda/tools/kernel_compare.cpp "src/opencl.cpp" "src/cl_kernels.S")
        add_dependencies(kernel_compare_opencl AUTOGENERATE)
        target_link_libraries(kernel_compare_opencl OpenCL::OpenCL)
    endif()
endif()
