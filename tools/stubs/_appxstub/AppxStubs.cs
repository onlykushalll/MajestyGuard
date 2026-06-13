// AppxStubs.cs — Complete Microsoft.Build.AppxPackage task stubs
// Generated from full analysis of MrtCore.PriGen.targets (1675 lines).
// Covers every task invocation that runs for an unpackaged WinUI 3 app.

using Microsoft.Build.Framework;
using Microsoft.Build.Utilities;

namespace Microsoft.Build.AppxPackage
{
    // ── ExpandPayloadDirectories ──────────────────────────────────────
    // Invocations in targets file:
    //   GetMrtPackagingOutputs: Inputs, VsTelemetrySession → Expanded
    //   GetMrtPackagingOutputs (2nd): Inputs, TargetDirsToExclude,
    //     TargetFilesToExclude, VsTelemetrySession → Expanded
    //   AddPriPayloadFilesToCopyToOutputDirectoryItems (gated AppxGeneratePriEnabled):
    //     Inputs, MakePriExeFullPath, MakePriExtensionPath, IntermediateDirectory,
    //     AdditionalMakepriExeParameters, ExcludeXamlFromLibraryLayoutsWhenXbfIsPresent,
    //     VsTelemetrySession → Expanded, IntermediateFileWrites
    public class ExpandPayloadDirectories : Task
    {
        public ITaskItem[]? Inputs                                        { get; set; }
        public ITaskItem[]? TargetDirsToExclude                           { get; set; }
        public ITaskItem[]? TargetFilesToExclude                          { get; set; }
        public string?       VsTelemetrySession                            { get; set; }
        public string?       MakePriExeFullPath                            { get; set; }
        public string?       MakePriExtensionPath                          { get; set; }
        public string?       IntermediateDirectory                          { get; set; }
        public string?       AdditionalMakepriExeParameters                 { get; set; }
        public string?       ExcludeXamlFromLibraryLayoutsWhenXbfIsPresent  { get; set; }
        [Output] public ITaskItem[] Expanded              { get; set; } = System.Array.Empty<ITaskItem>();
        [Output] public ITaskItem[] IntermediateFileWrites { get; set; } = System.Array.Empty<ITaskItem>();
        public override bool Execute()
        {
            Expanded               = Inputs ?? System.Array.Empty<ITaskItem>();
            IntermediateFileWrites = System.Array.Empty<ITaskItem>();
            return true;
        }
    }

    // ── RemovePayloadDuplicates ───────────────────────────────────────
    // Invocations:
    //   CopyLocalFilesOutputGroup: Inputs, ProjectName, Platform, VsTelemetrySession → Filtered
    //   _GeneratePrisForPortableLibraries (gated AppxPackage=true): same
    public class RemovePayloadDuplicates : Task
    {
        public ITaskItem[]? Inputs             { get; set; }
        public string?       ProjectName        { get; set; }
        public string?       Platform           { get; set; }
        public string?       VsTelemetrySession { get; set; }
        [Output] public ITaskItem[] Filtered              { get; set; } = System.Array.Empty<ITaskItem>();
        [Output] public ITaskItem[] IntermediateFileWrites { get; set; } = System.Array.Empty<ITaskItem>();
        public override bool Execute()
        {
            Filtered               = Inputs ?? System.Array.Empty<ITaskItem>();
            IntermediateFileWrites = System.Array.Empty<ITaskItem>();
            return true;
        }
    }

    // ── GetDefaultResourceLanguage ────────────────────────────────────
    // Invocation in _GetDefaultResourceLanguage (always runs via PrepareForRunDependsOn):
    //   DefaultLanguage, SourceAppxManifest, VsTelemetrySession → DefaultResourceLanguage
    public class GetDefaultResourceLanguage : Task
    {
        public string?       DefaultLanguage    { get; set; }
        public ITaskItem[]?  SourceAppxManifest { get; set; }
        public string?       VsTelemetrySession { get; set; }
        [Output] public string DefaultResourceLanguage { get; set; } = "en-US";
        public override bool Execute()
        {
            // Return the DefaultLanguage passed in (or fallback to en-US)
            DefaultResourceLanguage = !string.IsNullOrEmpty(DefaultLanguage)
                ? DefaultLanguage
                : "en-US";
            return true;
        }
    }

    // ── GetPackageArchitecture ────────────────────────────────────────
    // Invocation in _GetPackageArchitecture:
    //   Platform, ProjectArchitecture, RecursiveProjectArchitecture,
    //   VsTelemetrySession → PackageArchitecture
    public class GetPackageArchitecture : Task
    {
        public string?       Platform                     { get; set; }
        public ITaskItem[]?  ProjectArchitecture          { get; set; }
        public ITaskItem[]?  RecursiveProjectArchitecture { get; set; }
        public string?       VsTelemetrySession           { get; set; }
        [Output] public string PackageArchitecture { get; set; } = "neutral";
        public override bool Execute()
        {
            PackageArchitecture = Platform switch
            {
                "x64"    => "x64",
                "x86"    => "x86",
                "arm"    => "arm",
                "arm64"  => "arm64",
                "Win32"  => "x86",
                _        => "neutral",
            };
            return true;
        }
    }

    // ── ValidateConfiguration ─────────────────────────────────────────
    // Invocation in _ValidateConfiguration:
    //   TargetPlatformMinVersion, TargetPlatformVersion, ProjectLanguage,
    //   VsTelemetrySession, TargetPlatformIdentifier, Platform
    public class ValidateConfiguration : Task
    {
        public string? TargetPlatformMinVersion { get; set; }
        public string? TargetPlatformVersion    { get; set; }
        public string? ProjectLanguage          { get; set; }
        public string? VsTelemetrySession       { get; set; }
        public string? TargetPlatformIdentifier { get; set; }
        public string? Platform                 { get; set; }
        public override bool Execute() => true;  // No-op validation
    }

    // ── GetSdkFileFullPath ────────────────────────────────────────────
    // Invocation in _GetSdkToolsPathsFromSdk (for AppxPackaging + MrmSupport):
    //   FileName, FullFilePath, FileArchitecture, TargetPlatformSdkRootOverride,
    //   SDKIdentifier, SDKVersion, TargetPlatformIdentifier, TargetPlatformMinVersion,
    //   TargetPlatformVersion, MSBuildExtensionsPath64Exists, VsTelemetrySession,
    //   RequireExeExtension → ActualFullFilePath, ActualFileArchitecture
    public class GetSdkFileFullPath : Task
    {
        public string? FileName                      { get; set; }
        public string? FullFilePath                  { get; set; }
        public string? FileArchitecture              { get; set; }
        public bool    RequireExeExtension           { get; set; }
        public string? TargetPlatformSdkRootOverride { get; set; }
        public string? SDKIdentifier                 { get; set; }
        public string? SDKVersion                    { get; set; }
        public string? TargetPlatformIdentifier      { get; set; }
        public string? TargetPlatformMinVersion      { get; set; }
        public string? TargetPlatformVersion         { get; set; }
        public string? MSBuildExtensionsPath64Exists  { get; set; }
        public string? VsTelemetrySession            { get; set; }
        [Output] public string ActualFullFilePath    { get; set; } = "";
        [Output] public string ActualFileArchitecture { get; set; } = "";
        public override bool Execute()
        {
            ActualFullFilePath     = FullFilePath ?? "";
            ActualFileArchitecture = FileArchitecture ?? "";
            return true;
        }
    }

    // ── GetSdkPropertyValue ───────────────────────────────────────────
    // Invocation in _GetSdkToolsPathsFromSdk:
    //   TargetPlatformSdkRootOverride, SDKIdentifier, SDKVersion,
    //   TargetPlatformIdentifier, TargetPlatformMinVersion, TargetPlatformVersion,
    //   PropertyName, VsTelemetrySession → PropertyValue
    public class GetSdkPropertyValue : Task
    {
        public string? TargetPlatformSdkRootOverride { get; set; }
        public string? SDKIdentifier                 { get; set; }
        public string? SDKVersion                    { get; set; }
        public string? TargetPlatformIdentifier      { get; set; }
        public string? TargetPlatformMinVersion      { get; set; }
        public string? TargetPlatformVersion         { get; set; }
        public string? PropertyName                  { get; set; }
        public string? VsTelemetrySession            { get; set; }
        [Output] public string PropertyValue { get; set; } = "";
        public override bool Execute() { PropertyValue = ""; return true; }
    }

    // ── RemoveRedundantXamlFilesFromSdkPayload ────────────────────────
    // Registered as UsingTask — stub in case it gets invoked.
    public class RemoveRedundantXamlFilesFromSdkPayload : Task
    {
        public ITaskItem[]? Inputs             { get; set; }
        public string?       VsTelemetrySession { get; set; }
        [Output] public ITaskItem[] Filtered { get; set; } = System.Array.Empty<ITaskItem>();
        public override bool Execute()
        {
            Filtered = Inputs ?? System.Array.Empty<ITaskItem>();
            return true;
        }
    }
}