using Microsoft.Build.Framework;
using Microsoft.Build.Utilities;

namespace Microsoft.Build.Packaging.Pri.Tasks
{
    public class ExpandPriContent : Task
    {
        public ITaskItem[] Inputs { get; set; }
        public string MakePriExeFullPath { get; set; }
        public string MakePriExtensionPath { get; set; }
        public string IntermediateDirectory { get; set; }
        public string AdditionalMakepriExeParameters { get; set; }
        public string ExcludeXamlFromLibraryLayoutsWhenXbfIsPresent { get; set; }
        public string VsTelemetrySession { get; set; }
        [Output] public ITaskItem[] Expanded { get; set; } = new ITaskItem[0];
        [Output] public ITaskItem[] IntermediateFileWrites { get; set; } = new ITaskItem[0];
        public override bool Execute()
        {
            Expanded = Inputs ?? new ITaskItem[0];
            IntermediateFileWrites = new ITaskItem[0];
            return true;
        }
    }

    public class RemoveDuplicatePriFiles : Task
    {
        public ITaskItem[] Inputs { get; set; }
        public string Platform { get; set; }
        public string VsTelemetrySession { get; set; }
        [Output] public ITaskItem[] Filtered { get; set; } = new ITaskItem[0];
        public override bool Execute()
        {
            Filtered = Inputs ?? new ITaskItem[0];
            return true;
        }
    }

    public class CreatePriConfigXmlForSplitting : Task
    {
        public string VsTelemetrySession { get; set; }
        public override bool Execute() { return true; }
    }

    public class CreatePriConfigXmlForMainPackageFileMap : Task
    {
        public string VsTelemetrySession { get; set; }
        public override bool Execute() { return true; }
    }

    public class CreatePriConfigXmlForFullIndex : Task
    {
        public string VsTelemetrySession { get; set; }
        public string PriConfigXmlPath { get; set; }
        public string DefaultResourceLanguage { get; set; }
        public string DefaultResourceQualifiers { get; set; }
        public string PriInitialPath { get; set; }
        public string IntermediateExtension { get; set; }
        public string LayoutResfilesPath { get; set; }
        public string ResourcesResfilesPath { get; set; }
        public string PriResfilesPath { get; set; }
        public string EmbedFileResfilePath { get; set; }
        public string PriConfigXmlDefaultSnippetPath { get; set; }
        public string TargetPlatformIdentifier { get; set; }
        public string TargetPlatformVersion { get; set; }
        public ITaskItem[] AdditionalResourceResFiles { get; set; }
        public override bool Execute() { return true; }
    }

    public class CreatePriFilesForPortableLibraries : Task
    {
        public string MakePriExeFullPath { get; set; }
        public string MakePriExtensionPath { get; set; }
        public ITaskItem[] ContentToIndex { get; set; }
        public string IntermediateDirectory { get; set; }
        public string AdditionalMakepriExeParameters { get; set; }
        public string DefaultResourceLanguage { get; set; }
        public string DefaultResourceQualifiers { get; set; }
        public string IntermediateExtension { get; set; }
        public string TargetPlatformIdentifier { get; set; }
        public string TargetPlatformVersion { get; set; }
        public string AppxBundleAutoResourcePackageQualifiers { get; set; }
        public string SkipIntermediatePriGenerationForResourceFiles { get; set; }
        public string VsTelemetrySession { get; set; }
        [Output] public ITaskItem[] IntermediateFileWrites { get; set; } = new ITaskItem[0];
        [Output] public ITaskItem[] CreatedPriFiles { get; set; } = new ITaskItem[0];
        [Output] public ITaskItem[] UnprocessedReswFiles_DefaultLanguage { get; set; } = new ITaskItem[0];
        [Output] public ITaskItem[] UnprocessedReswFiles_OtherLanguages { get; set; } = new ITaskItem[0];
        public override bool Execute() { return true; }
    }

    public class GenerateMainPriConfigurationFile : Task
    {
        public string VsTelemetrySession { get; set; }
        public override bool Execute() { return true; }
    }

    public class GeneratePriConfigurationFiles : Task
    {
        public string UnfilteredLayoutResfilesPath { get; set; }
        public string FilteredLayoutResfilesPath { get; set; }
        public string ExcludedLayoutResfilesPath { get; set; }
        public string ResourcesResfilesPath { get; set; }
        public string PriResfilesPath { get; set; }
        public string EmbedFileResfilePath { get; set; }
        public ITaskItem[] LayoutFiles { get; set; }
        public ITaskItem[] PRIResourceFiles { get; set; }
        public ITaskItem[] PriFiles { get; set; }
        public ITaskItem[] EmbedFiles { get; set; }
        public string IntermediateExtension { get; set; }
        public ITaskItem[] UnprocessedResourceFiles_OtherLanguages { get; set; }
        public string VsTelemetrySession { get; set; }
        [Output] public ITaskItem[] AdditionalResourceResFiles { get; set; } = new ITaskItem[0];
        public override bool Execute() { return true; }
    }

    public class GenerateProjectPriFile : Task
    {
        public string MakePriExeFullPath { get; set; }
        public string MakePriExtensionPath { get; set; }
        public string PriConfigXmlPath { get; set; }
        public string IndexFilesForQualifiersCollection { get; set; }
        public string ProjectPriIndexName { get; set; }
        public string InsertReverseMap { get; set; }
        public string ProjectDirectory { get; set; }
        public string OutputFileName { get; set; }
        public string QualifiersPath { get; set; }
        public string IntermediateExtension { get; set; }
        public string AppxBundleAutoResourcePackageQualifiers { get; set; }
        public string MultipleQualifiersPerDimensionFoundPath { get; set; }
        public string AdditionalMakepriExeParameters { get; set; }
        public string VsTelemetrySession { get; set; }
        public override bool Execute() { return true; }
    }

    public class UpdateMainPackageFileMap : Task
    {
        public string VsTelemetrySession { get; set; }
        public override bool Execute() { return true; }
    }
}
